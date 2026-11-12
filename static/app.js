"use strict";
const $ = (s) => document.querySelector(s);
const STATUS_RU = { queued: "в очереди", transcribing: "расшифровка", analyzing: "анализ", done: "готово", error: "ошибка" };
const ACTIVE = ["queued", "transcribing", "analyzing"];
let selectedFiles = [];
let pollTimer = null;
let currentJobId = null;
let lastReportLen = -1;

/* ---------- Общее ---------- */
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}
const fmtDur = (s) => { s = Math.round(s || 0); const m = Math.floor(s / 60); return m ? `${m} мин ${s % 60} с` : `${s} с`; };
const fmtClock = (s) => { const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = Math.floor(s % 60);
  return (h ? h + ":" : "") + String(m).padStart(2, "0") + ":" + String(x).padStart(2, "0"); };
const fmtDate = (t) => new Date(t * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function loadConfig() {
  try {
    const c = await api("/api/config");
    $("#cfg").innerHTML =
      `<span class="pill ${c.stt_ready ? "ok" : "bad"}">Распознавание: AssemblyAI · ${esc(c.stt_model)}</span>` +
      `<span class="pill ${c.llm_ready ? "ok" : "bad"}">Анализ: ${esc(c.llm)}</span>`;
  } catch (_) {}
}

async function loadHistory() {
  try {
    const list = await api("/api/jobs");
    $("#history").innerHTML = list.length ? list.map((j) => {
      const name = j.manager_name || (j.files[0] || "").replace(/^\d+_/, "") || "Встреча";
      return `<li><a href="#job=${j.id}" class="${j.id === currentJobId ? "current" : ""}">${esc(name)}
        <div class="st">${fmtDate(j.created)} · ${STATUS_RU[j.status] || j.status}${j.duration ? " · " + fmtDur(j.duration) : ""}</div></a></li>`;
    }).join("") : '<li class="muted">Пока пусто</li>';
  } catch (_) {}
}

function metaForm() {
  const fd = new FormData();
  document.querySelectorAll(".meta [name]").forEach((el) => fd.append(el.name, el.value));
  return fd;
}

function showError(el, msg) { el.textContent = msg; el.hidden = !msg; }

/* ---------- Отправка ---------- */
function upload(files, label) {
  return new Promise((resolve, reject) => {
    const fd = metaForm();
    files.forEach((f) => fd.append("files", f, f.name));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) $("#job-detail").textContent = `${label}… ${Math.round(e.loaded / e.total * 100)}%`;
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (_) {}
      xhr.status >= 200 && xhr.status < 300 ? resolve(data.id) : reject(new Error(data.detail || xhr.statusText));
    };
    xhr.onerror = () => reject(new Error("Сервер недоступен"));
    xhr.send(fd);
  });
}

// groups: массив встреч, каждая — массив файлов
async function submit(groups) {
  showError($("#new-error"), "");
  openJobView(null);
  renderSteps("upload");
  try {
    const ids = [];
    for (const [i, files] of groups.entries()) {
      const label = groups.length > 1 ? `Загрузка встречи ${i + 1} из ${groups.length}` : "Загрузка файлов на сервер";
      ids.push(await upload(files, label));
    }
    location.hash = "job=" + ids[0];
  } catch (e) {
    location.hash = "";
    showError($("#new-error"), "Не удалось отправить: " + e.message);
  }
}

/* ---------- Файлы ---------- */
function setFiles(list) {
  selectedFiles = [...list].sort((a, b) => a.name.localeCompare(b.name, "ru", { numeric: true }));
  $("#file-list").innerHTML = selectedFiles.map((f) =>
    `<li><span>${esc(f.name)}</span><span class="muted">${(f.size / 1048576).toFixed(1)} МБ</span></li>`).join("");
  $("#btn-upload").disabled = !selectedFiles.length;
  $("#mode").hidden = selectedFiles.length < 2;
  updateUploadLabel();
}
const joined = () => document.querySelector('input[name="mode"]:checked').value === "joined";
function updateUploadLabel() {
  const n = selectedFiles.length;
  $("#btn-upload").textContent = n < 2 ? "Анализировать файл" : joined() ? `Анализировать встречу из ${n} частей` : `Анализировать ${n} встречи`;
}
document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", updateUploadLabel));
$("#file-input").addEventListener("change", (e) => setFiles(e.target.files));
const drop = $("#drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => setFiles(e.dataTransfer.files));
$("#btn-upload").addEventListener("click", () => {
  if (!selectedFiles.length) return;
  submit(joined() ? [selectedFiles] : selectedFiles.map((f) => [f]));
});

/* ---------- Запись с микрофона ---------- */
const rec = { mr: null, stream: null, chunks: [], started: 0, timer: null, ctx: null, raf: null, wake: null, cancel: false };

function pickMime() {
  const opts = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  return opts.find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || "";
}

async function startRecording() {
  showError($("#new-error"), "");
  if (!navigator.mediaDevices?.getUserMedia) {
    return showError($("#new-error"), "Браузер не даёт доступ к микрофону. Откройте приложение по https или через localhost.");
  }
  try {
    rec.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) {
    return showError($("#new-error"), "Нет доступа к микрофону: " + e.message);
  }
  const mime = pickMime();
  rec.mr = new MediaRecorder(rec.stream, mime ? { mimeType: mime } : undefined);
  rec.chunks = []; rec.cancel = false;
  rec.mr.ondataavailable = (e) => e.data.size && rec.chunks.push(e.data);
  rec.mr.onstop = onRecordingStop;
  rec.mr.start(1000);
  rec.started = Date.now();

  // Индикатор громкости — видно, что микрофон действительно слышит
  rec.ctx = new AudioContext();
  const an = rec.ctx.createAnalyser(); an.fftSize = 512;
  rec.ctx.createMediaStreamSource(rec.stream).connect(an);
  const buf = new Uint8Array(an.fftSize);
  const draw = () => {
    an.getByteTimeDomainData(buf);
    let peak = 0; for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
    $("#meter-bar").style.width = Math.min(100, peak / 128 * 180) + "%";
    rec.raf = requestAnimationFrame(draw);
  };
  draw();
  try { rec.wake = await navigator.wakeLock?.request("screen"); } catch (_) {}

  $("#recorder").classList.add("recording");
  $("#rec-actions").hidden = false; $("#meter").hidden = false;
  $("#rec-sub").textContent = "Идёт запись. Не закрывайте вкладку.";
  rec.timer = setInterval(() => { $("#rec-title").textContent = "Запись " + fmtClock((Date.now() - rec.started) / 1000); }, 250);
}

function stopRecording(cancel) {
  if (!rec.mr) return;
  rec.cancel = cancel;
  rec.mr.stop();
  rec.stream.getTracks().forEach((t) => t.stop());
  clearInterval(rec.timer); cancelAnimationFrame(rec.raf);
  rec.ctx?.close(); rec.wake?.release?.();
  $("#recorder").classList.remove("recording");
  $("#rec-actions").hidden = true; $("#meter").hidden = true;
  $("#rec-title").textContent = "Начать запись";
  $("#rec-sub").textContent = "Используется микрофон этого устройства";
}

function onRecordingStop() {
  const mr = rec.mr; rec.mr = null;
  if (rec.cancel || !rec.chunks.length) return;
  const type = mr.mimeType || "audio/webm";
  const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
  const stamp = new Date().toISOString().slice(0, 16).replace("T", "_").replace(":", "-");
  submit([[new File(rec.chunks, `запись_${stamp}.${ext}`, { type })]]);
}

$("#btn-rec").addEventListener("click", () => (rec.mr ? stopRecording(false) : startRecording()));
$("#btn-stop").addEventListener("click", () => stopRecording(false));
$("#btn-cancel").addEventListener("click", () => stopRecording(true));
window.addEventListener("beforeunload", (e) => { if (rec.mr) { e.preventDefault(); e.returnValue = ""; } });

/* ---------- Просмотр встречи ---------- */
function openJobView(id) {
  currentJobId = id; lastReportLen = -1;
  $("#view-new").hidden = true; $("#view-job").hidden = false;
  $("#job-title").textContent = "Встреча"; $("#job-sub").textContent = "";
  ["#score", "#metrics", "#tabs"].forEach((s) => ($(s).hidden = true));
  showError($("#job-error"), ""); showError($("#job-warn"), "");
  $("#pane-report").innerHTML = ""; $("#pane-transcript").textContent = "";
  transcriptSig = ""; player.audio.pause(); player.sig = ""; $("#player").hidden = true;
  document.querySelectorAll("[data-text]").forEach((el) => (el.textContent = ""));
  switchTab("report");
}

function renderSteps(status) {
  const order = ["upload", "transcribing", "analyzing", "done"];
  const cur = status === "queued" ? 1 : order.indexOf(status);
  document.querySelectorAll("#steps li").forEach((li, i) => {
    li.className = i < cur || status === "done" ? "done" : i === cur ? "active" : "";
  });
}

function extractScore(md) {
  const m = md.match(/Итог[^0-9\n]{0,25}(\d{1,3}(?:[.,]\d+)?)\s*\/\s*100/i);
  return m ? m[1].replace(",", ".") : null;
}

/* ---------- Расшифровка по репликам и плеер ---------- */
const toSec = (ts) => ts.split(":").reduce((acc, x) => acc * 60 + Number(x), 0);

function speakerKey(u, nParts) { return nParts > 1 ? `${u.part}:${u.speaker}` : u.speaker; }
function speakerLabel(j, u) {
  const n = j.files.length, roles = (j.roles && j.roles.roles) || {};
  return roles[speakerKey(u, n)] || (n > 1 ? `Спикер ${u.speaker} (ч. ${u.part})` : `Спикер ${u.speaker}`);
}

let transcriptSig = "";
function renderTranscript(j) {
  const utts = j.utterances || [];
  const sig = j.id + utts.length + JSON.stringify(j.roles || {});
  if (sig === transcriptSig) return;
  transcriptSig = sig;
  const pane = $("#pane-transcript");
  if (!utts.length) { pane.textContent = j.transcript || ""; return; }
  // Цвет закрепляется за ролью: первый по объёму речи участник (обычно менеджер) — акцентный
  const order = (j.metrics || []).map((m) => m.speaker);
  const colorOf = (label) => { const i = order.indexOf(label); return `role-${i < 0 ? 3 : Math.min(i, 3)}`; };
  let html = "", part = null;
  for (const [i, u] of utts.entries()) {
    if (j.files.length > 1 && u.part !== part) {
      part = u.part;
      html += `<div class="part-sep">Часть ${part} · ${esc(j.files[part - 1].replace(/^\d+_/, ""))}</div>`;
    }
    const label = speakerLabel(j, u);
    html += `<div class="utt ${colorOf(label)}" data-i="${i}" data-start="${u.start}">
      <button class="ts" type="button" data-t="${u.start}">${fmtClock(u.start)}</button>
      <span class="who">${esc(label)}</span><p>${esc(u.text)}</p></div>`;
  }
  pane.innerHTML = html;
}

// [00:12:40] и 00:12:40 в отчёте → кнопки, которые включают запись с этого места
function linkifyTimestamps(root) {
  const re = /\[?(\d{1,2}:\d{2}:\d{2})\]?|\[(\d{1,2}:\d{2})\]/g;
  const has = new RegExp(re.source);  // без флага g: test() не помнит lastIndex между узлами
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (has.test(walker.currentNode.nodeValue)) nodes.push(walker.currentNode);
  for (const node of nodes) {
    re.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0, m;
    const text = node.nodeValue;
    while ((m = re.exec(text))) {
      const ts = m[1] || m[2];
      frag.append(text.slice(last, m.index));
      const b = document.createElement("button");
      b.type = "button"; b.className = "ts-link"; b.dataset.t = toSec(ts); b.textContent = "▶ " + ts;
      b.title = "Прослушать с этого места";
      frag.append(b);
      last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

const player = {
  job: null, part: -1, pending: null, sig: "",
  audio: $("#audio"),
  total() { const p = this.job.parts; return p.length ? p[p.length - 1].offset + p[p.length - 1].duration : this.job.duration; },
  now() { return this.part < 0 ? 0 : this.job.parts[this.part].offset + (this.audio.currentTime || 0); },
  setup(j) {
    const sig = j.id + (j.parts || []).length;
    if (!(j.parts || []).length) { $("#player").hidden = true; return; }
    $("#player").hidden = false;
    this.job = j;
    if (sig === this.sig) return;
    this.sig = sig; this.part = -1; this.audio.removeAttribute("src");
    $("#seek").max = Math.round(this.total());
    $("#dur").textContent = fmtClock(this.total());
    this.tick();
  },
  load(index, offsetSec, play) {
    if (index !== this.part) {
      this.part = index;
      this.audio.src = `/api/jobs/${this.job.id}/audio/${index}`;
      this.pending = { offsetSec, play };
      return;
    }
    this.audio.currentTime = offsetSec;
    if (play) this.audio.play();
  },
  seek(t, play = true) {
    const parts = this.job.parts;
    let i = parts.findIndex((p) => t < p.offset + p.duration);
    if (i < 0) i = parts.length - 1;
    this.load(i, Math.max(0, t - parts[i].offset), play);
  },
  toggle() {
    if (this.part < 0) return this.seek(0);
    this.audio.paused ? this.audio.play() : this.audio.pause();
  },
  tick() {
    const t = this.now();
    $("#seek").value = t;
    $("#time").textContent = fmtClock(t);
    const utts = this.job.utterances || [];
    let lo = 0, hi = utts.length - 1, idx = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (utts[mid].start <= t) { idx = mid; lo = mid + 1; } else hi = mid - 1; }
    $("#now-speaker").textContent = idx >= 0 && this.part >= 0 ? speakerLabel(this.job, utts[idx]) : "";
    const prev = document.querySelector(".utt.active");
    const cur = idx >= 0 ? document.querySelector(`.utt[data-i="${idx}"]`) : null;
    if (prev !== cur) { prev?.classList.remove("active"); cur?.classList.add("active"); }
  },
};
player.audio.addEventListener("loadedmetadata", () => {
  if (!player.pending) return;
  const { offsetSec, play } = player.pending; player.pending = null;
  player.audio.currentTime = offsetSec;
  if (play) player.audio.play();
});
player.audio.addEventListener("timeupdate", () => player.tick());
player.audio.addEventListener("play", () => ($("#btn-play").textContent = "❚❚"));
player.audio.addEventListener("pause", () => ($("#btn-play").textContent = "▶"));
player.audio.addEventListener("ended", () => {
  if (player.part < player.job.parts.length - 1) player.load(player.part + 1, 0, true);
});
$("#btn-play").addEventListener("click", () => player.toggle());
$("#seek").addEventListener("input", (e) => player.seek(Number(e.target.value), !player.audio.paused || player.part < 0));
$("#btn-rate").addEventListener("click", () => {
  const rates = [1, 1.25, 1.5, 2], next = rates[(rates.indexOf(player.audio.playbackRate) + 1) % rates.length];
  player.audio.playbackRate = next; $("#btn-rate").textContent = next + "×";
});
document.addEventListener("click", (e) => {
  const b = e.target.closest(".ts, .ts-link");
  if (b && player.job) player.seek(Number(b.dataset.t));
});

function renderTexts(j) {
  const texts = j.texts || {};
  document.querySelectorAll("[data-text]").forEach((el) => {
    const t = texts[el.dataset.text];
    el.textContent = t || (ACTIVE.includes(j.status) ? "Появится, когда отчёт будет готов." :
      j.report ? "Раздел не найден в отчёте." : "—");
    el.classList.toggle("empty", !t);
  });
  document.querySelectorAll("[data-copy]").forEach((b) => (b.disabled = !texts[b.dataset.copy]));
}

function renderMetrics(rows) {
  $("#metrics").hidden = !rows.length;
  $("#metrics-body").innerHTML = rows.map((r) =>
    `<tr><td>${esc(r.speaker)}</td><td><div class="bar"><i style="width:${r.share}px"></i>${r.share}%</div></td>
     <td>${r.turns}</td><td>${r.questions}</td><td>${fmtDur(r.longest_sec)}</td></tr>`).join("");
}

function renderJob(j) {
  const names = j.files.map((f) => f.replace(/^\d+_/, ""));
  $("#job-title").textContent = j.meta.manager_name ? `Встреча: ${j.meta.manager_name}` : (names.length > 1 ? `Встреча из ${names.length} частей` : names[0] || "Встреча");
  $("#job-sub").textContent = [fmtDate(j.created), j.duration ? "длительность " + fmtDur(j.duration) : "", j.llm].filter(Boolean).join(" · ");

  if (j.status === "error") {
    const failed = j.transcript ? 2 : 1;
    document.querySelectorAll("#steps li").forEach((li, i) => (li.className = i < failed ? "done" : i === failed ? "failed" : ""));
  } else {
    renderSteps(j.status);
  }
  $("#job-detail").textContent = ACTIVE.includes(j.status) ? j.detail + "…" : "";
  showError($("#job-error"), j.status === "error" ? j.error : "");
  showError($("#job-warn"), j.warnings.join("\n"));
  renderMetrics(j.metrics || []);

  const hasContent = j.transcript || j.report;
  $("#tabs").hidden = !hasContent;
  $("#dl-report").href = `/api/jobs/${j.id}/report.md`;
  $("#dl-transcript").href = `/api/jobs/${j.id}/transcript.txt`;
  $("#dl-report").hidden = !j.report;
  $("#btn-reanalyze").hidden = !j.transcript || ACTIVE.includes(j.status);
  renderTranscript(j);
  player.setup(j);

  if (j.report.length !== lastReportLen) {
    lastReportLen = j.report.length;
    $("#pane-report").innerHTML = j.report ? DOMPurify.sanitize(marked.parse(j.report)) :
      (j.transcript ? '<p class="muted">Расшифровка готова, модель формирует отчёт…</p>' : "");
    linkifyTimestamps($("#pane-report"));
    const score = extractScore(j.report);
    $("#score").hidden = !score; if (score) $("#score-val").textContent = score;
  }
  renderTexts(j);
  $("#pane-report").classList.toggle("streaming", j.status === "analyzing");
}

async function poll(id) {
  clearTimeout(pollTimer);
  if (id !== currentJobId) return;
  try {
    const j = await api("/api/jobs/" + id);
    if (id !== currentJobId) return;
    renderJob(j);
    if (ACTIVE.includes(j.status)) pollTimer = setTimeout(() => poll(id), 1500);
    else loadHistory();
  } catch (e) {
    showError($("#job-error"), e.message);
  }
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("#pane-report").hidden = name !== "report";
  $("#pane-texts").hidden = name !== "texts";
  $("#pane-transcript").hidden = name !== "transcript";
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

document.querySelectorAll("[data-copy]").forEach((btn) => btn.addEventListener("click", async () => {
  const text = document.querySelector(`[data-text="${btn.dataset.copy}"]`).textContent;
  try { await navigator.clipboard.writeText(text); btn.textContent = "Скопировано ✓"; }
  catch (_) { btn.textContent = "Не удалось, выделите вручную"; }
  setTimeout(() => (btn.textContent = "Скопировать"), 2000);
}));

$("#btn-reanalyze").addEventListener("click", async () => {
  try { await api(`/api/jobs/${currentJobId}/reanalyze`, { method: "POST" }); lastReportLen = -1; poll(currentJobId); }
  catch (e) { showError($("#job-error"), e.message); }
});

/* ---------- Навигация ---------- */
function route() {
  const m = location.hash.match(/job=([\w-]+)/);
  clearTimeout(pollTimer);
  if (m) { openJobView(m[1]); poll(m[1]); }
  else if (!rec.mr) { currentJobId = null; $("#view-job").hidden = true; $("#view-new").hidden = false; }
  loadHistory();
}
window.addEventListener("hashchange", route);
loadConfig();
route();
