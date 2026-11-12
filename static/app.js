"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const STATUS_RU = { queued: "в очереди", transcribing: "расшифровка", analyzing: "анализ", done: "готово", error: "ошибка" };
const ACTIVE = ["queued", "transcribing", "analyzing"];
let selectedFiles = [];
let pollTimer = null;
let currentJobId = null;
let currentJob = null;
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
const fmtDur = (s) => { s = Math.round(s || 0); const m = Math.floor(s / 60); return m ? `${m} мин` : `${s} с`; };
const fmtClock = (s) => { s = Math.max(0, s || 0); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = Math.floor(s % 60);
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0"); };
const fmtDate = (t) => new Date(t * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const scoreClass = (s) => (s == null ? "" : s >= 80 ? "good" : s >= 60 ? "mid" : "low");
const ICON = {
  check: '<svg viewBox="0 0 24 24"><path d="M5 12l5 5L20 7"/></svg>',
  alert: '<svg viewBox="0 0 24 24"><path d="M12 8v5M12 17h.01"/></svg>',
  spark: '<svg viewBox="0 0 24 24"><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/></svg>',
  play: '<path d="M8 5v14l11-7z"/>',
  pause: '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>',
};

async function loadConfig() {
  try {
    const c = await api("/api/config");
    $("#cfg").innerHTML =
      `<span class="${c.stt_ready ? "" : "bad"}">Распознавание: AssemblyAI</span>` +
      `<span class="${c.llm_ready ? "" : "bad"}">Анализ: ${esc(c.llm)}</span>`;
  } catch (_) {}
}

async function loadHistory() {
  try {
    const list = await api("/api/jobs");
    $("#history").innerHTML = list.length ? list.map((j) => {
      const name = j.client_name ? `Клиент: ${j.client_name}` : j.manager_name || (j.files[0] || "").replace(/^\d+_/, "") || "Встреча";
      const meta = [fmtDate(j.created), j.duration ? fmtDur(j.duration) : STATUS_RU[j.status]].join(" · ");
      const score = j.score != null ? `<span class="h-score ${scoreClass(j.score)}">${Math.round(j.score)}</span>`
        : `<span class="h-score">${ACTIVE.includes(j.status) ? "…" : "—"}</span>`;
      return `<li><a href="#job=${j.id}" class="${j.id === currentJobId ? "current" : ""}">
        <span class="h-name">${esc(name)}</span>${score}<span class="h-meta">${esc(meta)}</span></a></li>`;
    }).join("") : '<li class="empty">Пока пусто</li>';
  } catch (_) {}
}

function metaForm() {
  const fd = new FormData();
  $$(".meta [name]").forEach((el) => fd.append(el.name, el.value));
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
    `<li><span>${esc(f.name)}</span><span class="sub">${(f.size / 1048576).toFixed(1)} МБ</span></li>`).join("");
  $("#btn-upload").disabled = !selectedFiles.length;
  $("#mode").hidden = selectedFiles.length < 2;
  updateUploadLabel();
}
const joined = () => document.querySelector('input[name="mode"]:checked').value === "joined";
function updateUploadLabel() {
  const n = selectedFiles.length;
  $("#btn-upload").textContent = n < 2 ? "Анализировать файл" : joined() ? `Анализировать встречу из ${n} частей` : `Анализировать ${n} встречи`;
}
$$('input[name="mode"]').forEach((r) => r.addEventListener("change", updateUploadLabel));
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

/* ---------- Разбор отчёта для сводки ---------- */
function sectionBody(md, n) {
  const lines = md.split("\n");
  const start = lines.findIndex((l) => new RegExp(`^#+\\s*\\**\\s*${n}\\.\\s`).test(l));
  if (start < 0) return "";
  const level = lines[start].match(/^#+/)[0].length;
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    const h = lines[i].match(/^(#+)\s/);
    if (h && h[1].length <= level) { end = i; break; }
  }
  return lines.slice(start + 1, end).join("\n");
}
const plain = (s) => s.replace(/\*\*|__|`/g, "").replace(/^\s*[-*+]\s+|^\s*\d+[.)]\s+/gm, "").replace(/\s+/g, " ").trim();
function firstPoint(body) {
  const blocks = body.split(/\n(?=\s*(?:[-*+]|\d+[.)])\s)|\n\s*\n/).map((b) => plain(b)).filter((b) => b.length > 25 && !/^[-|]/.test(b));
  return blocks[0] ? (blocks[0].length > 260 ? blocks[0].slice(0, 257) + "…" : blocks[0]) : "";
}
function reportField(md, label) {
  const m = md.match(new RegExp(label + "[^:\\n]{0,20}:\\s*\\**\\s*([^\\n]+)", "i"));
  return m ? plain(m[1]).replace(/^\**\s*/, "") : "";
}
function splitValue(v) {  // «близок к покупке, но ждёт замеров» → ["близок к покупке", "но ждёт замеров"]
  const m = v.match(/^([^,.;(—–]+)[,.;(—–]?\s*(.*)$/);
  return m ? [m[1].trim(), m[2].replace(/\)$/, "").trim()] : [v, ""];
}
const LEVELS = [[90, "Отличный уровень"], [80, "Хороший уровень"], [70, "Удовлетворительно"], [60, "Слабая встреча"], [0, "Неудовлетворительно"]];

function renderOverview(j) {
  const md = j.report || "";
  const has = j.score != null || (j.stages || []).length;
  $("#overview").hidden = !has;
  if (!has) return;

  const score = j.score;
  $("#score-val").textContent = score != null ? Math.round(score) : "—";
  $("#ring-val").style.strokeDashoffset = 326.7 * (1 - Math.min(100, score || 0) / 100);
  $("#score-level").textContent = score != null ? LEVELS.find(([t]) => score >= t)[1] : "";

  const [stage, stageSub] = splitValue(reportField(md, "Стадия принятия решения") || reportField(md, "Стадия") || "—");
  $("#tile-stage").textContent = stage; $("#tile-stage-sub").textContent = stageSub.slice(0, 90);
  const [prob, probSub] = splitValue(reportField(md, "Вероятность покупки") || "—");
  $("#tile-prob").textContent = prob; $("#tile-prob-sub").textContent = probSub.slice(0, 90);
  const metrics = j.metrics || [];
  const mgr = metrics.find((m) => /менеджер/i.test(m.speaker) && !/второй|коллега/i.test(m.speaker)) || metrics[0];
  const client = metrics.find((m) => /^клиент/i.test(m.speaker));
  $("#tile-talk").textContent = mgr ? `${Math.round(mgr.share)}% времени` : "—";
  $("#tile-talk-sub").textContent = mgr ? `${mgr.questions} вопросов${client ? ` · клиент ${Math.round(client.share)}%` : ""}` : "";

  $("#bars").innerHTML = (j.stages || []).map((s) => {
    const pct = s.got == null ? null : Math.round(s.got / s.max * 100);
    const cls = pct == null ? "na" : pct >= 80 ? "" : pct >= 50 ? "mid" : "low";
    const tip = `${s.stage}. ${s.title}: ${s.got == null ? "Н/П" : `${s.got} из ${s.max}`}${s.comment ? " — " + s.comment : ""}`;
    return `<div class="bar" title="${esc(tip)}"><span class="bar-val">${pct == null ? "Н/П" : pct + "%"}</span>
      <div class="bar-track"><div class="bar-col ${cls}" style="--pct:${pct == null ? 100 : Math.max(pct, 6)}%"></div></div>
      <span class="bar-name">${esc(s.title)}</span></div>`;
  }).join("");

  const items = [
    [3, "Что получилось", ICON.check, "var(--good)"],
    [4, "Главная ошибка", ICON.alert, "var(--warn)"],
    [7, "Упущенный момент", ICON.spark, "var(--accent)"],
  ].map(([n, title, icon, color]) => {
    const text = firstPoint(sectionBody(md, n));
    return text ? `<div class="insight"><div class="insight-head"><span class="insight-icon" style="background:${color}">${icon}</span>${title}</div>
      <p>${esc(text)}</p><button type="button" data-section="${n}">Подробнее в отчёте</button></div>` : "";
  }).join("");
  $("#insights").innerHTML = items || '<p class="sub">Появятся, когда отчёт будет готов.</p>';
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-section]");
  if (!b) return;
  switchTab("report");
  const h = $$("#pane-report h1, #pane-report h2, #pane-report h3").find((el) => el.textContent.trim().startsWith(b.dataset.section + "."));
  if (h) { h.scrollIntoView({ behavior: "smooth", block: "start" }); h.classList.remove("flash"); void h.offsetWidth; h.classList.add("flash"); }
});

/* ---------- Роли и расшифровка ---------- */
const toSec = (ts) => ts.split(":").reduce((acc, x) => acc * 60 + Number(x), 0);
function speakerKey(u, nParts) { return nParts > 1 ? `${u.part}:${u.speaker}` : u.speaker; }
function speakerLabel(j, u) {
  const n = j.files.length, roles = (j.roles && j.roles.roles) || {};
  return roles[speakerKey(u, n)] || (n > 1 ? `Спикер ${u.speaker} (ч. ${u.part})` : `Спикер ${u.speaker}`);
}
// Цвет за ролью: по убыванию доли речи (обычно менеджер — акцентный, клиент — зелёный)
function roleClass(j, label) {
  const order = (j.metrics || []).map((m) => m.speaker);
  const i = order.indexOf(label);
  return `r${i < 0 ? 3 : Math.min(i, 3)}`;
}

let transcriptSig = "";
function renderTranscript(j) {
  const utts = j.utterances || [];
  const sig = j.id + utts.length + JSON.stringify(j.roles || {});
  if (sig === transcriptSig) return;
  transcriptSig = sig;
  const pane = $("#pane-transcript");
  if (!utts.length) { pane.textContent = j.transcript || ""; return; }
  let html = "", part = null;
  for (const [i, u] of utts.entries()) {
    if (j.files.length > 1 && u.part !== part) {
      part = u.part;
      html += `<div class="part-sep">Часть ${part} · ${esc(j.files[part - 1].replace(/^\d+_/, ""))}</div>`;
    }
    const label = speakerLabel(j, u);
    html += `<div class="utt ${roleClass(j, label)}" data-i="${i}">
      <div class="utt-head"><span class="who">${esc(label)}</span><button class="ts" type="button" data-t="${u.start}">[${fmtClock(u.start)}]</button></div>
      <p>${esc(u.text)}</p></div>`;
  }
  pane.innerHTML = html;
  search.run();
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

/* ---------- Плеер ---------- */
const player = {
  job: null, part: -1, pending: null, sig: "",
  audio: $("#audio"),
  total() { const p = this.job.parts; return p.length ? p[p.length - 1].offset + p[p.length - 1].duration : this.job.duration; },
  now() { return this.part < 0 ? 0 : this.job.parts[this.part].offset + (this.audio.currentTime || 0); },
  setup(j) {
    if (!(j.parts || []).length) { $("#player").hidden = true; return; }
    $("#player").hidden = false;
    this.job = j;
    const sig = j.id + j.parts.length + JSON.stringify(j.roles || {}) + (j.utterances || []).length;
    if (sig === this.sig) return;
    const fresh = !this.sig.startsWith(j.id);
    this.sig = sig;
    if (fresh) { this.part = -1; this.audio.removeAttribute("src"); }
    $("#dur").textContent = fmtClock(this.total());
    this.renderTracks();
    this.tick();
  },
  renderTracks() {
    const j = this.job, total = this.total() || 1, by = new Map();
    for (const u of j.utterances || []) {
      const label = speakerLabel(j, u);
      if (!by.has(label)) by.set(label, []);
      const segs = by.get(label), last = segs[segs.length - 1];
      if (last && u.start - last[1] < 1.5) last[1] = u.end; else segs.push([u.start, u.end]);
    }
    const share = Object.fromEntries((j.metrics || []).map((m) => [m.speaker, m.share]));
    const labels = [...by.keys()].sort((a, b) => (share[b] || 0) - (share[a] || 0));
    $("#tracks").innerHTML = labels.map((label) => `<div class="track ${roleClass(j, label)}">
      <div class="track-label">${esc(label)}${share[label] != null ? `<span>${Math.round(share[label])}%</span>` : ""}</div>
      <div class="lane">${by.get(label).map(([s, e]) =>
        `<i class="seg" style="left:${(s / total * 100).toFixed(3)}%;width:${((e - s) / total * 100).toFixed(3)}%"></i>`).join("")}</div></div>`).join("");
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
    t = Math.max(0, Math.min(t, this.total() - 0.5));
    let i = parts.findIndex((p) => t < p.offset + p.duration);
    if (i < 0) i = parts.length - 1;
    this.load(i, Math.max(0, t - parts[i].offset), play);
    this.tick(t);
  },
  toggle() {
    if (this.part < 0) return this.seek(0);
    this.audio.paused ? this.audio.play() : this.audio.pause();
  },
  tick(forced) {
    if (!this.job) return;
    const t = forced ?? this.now(), frac = Math.min(1, t / (this.total() || 1));
    $("#time").textContent = fmtClock(t);
    $("#cursor-time").textContent = fmtClock(t);
    $("#progress-fill").style.width = frac * 100 + "%";
    $("#cursor").style.left = `calc(18px + (100% - 36px) * ${frac})`;
    const utts = this.job.utterances || [];
    let lo = 0, hi = utts.length - 1, idx = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (utts[mid].start <= t) { idx = mid; lo = mid + 1; } else hi = mid - 1; }
    const prev = $(".utt.active");
    const cur = idx >= 0 && this.part >= 0 ? $(`.utt[data-i="${idx}"]`) : null;
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
player.audio.addEventListener("play", () => ($("#play-icon").innerHTML = ICON.pause));
player.audio.addEventListener("pause", () => ($("#play-icon").innerHTML = ICON.play));
player.audio.addEventListener("ended", () => {
  if (player.part < player.job.parts.length - 1) player.load(player.part + 1, 0, true);
});
$("#btn-play").addEventListener("click", () => player.toggle());
$("#btn-back").addEventListener("click", () => player.seek(player.now() - 15, !player.audio.paused));
$("#btn-fwd").addEventListener("click", () => player.seek(player.now() + 15, !player.audio.paused || player.part < 0));
$("#btn-rate").addEventListener("click", () => {
  const rates = [1, 1.25, 1.5, 2], next = rates[(rates.indexOf(player.audio.playbackRate) + 1) % rates.length];
  player.audio.playbackRate = next; $("#btn-rate").textContent = next + "×";
});
$("#timeline").addEventListener("click", (e) => {
  const r = $("#timeline").getBoundingClientRect();
  const frac = Math.max(0, Math.min(1, (e.clientX - r.left - 18) / (r.width - 36)));
  player.seek(frac * player.total(), !player.audio.paused || player.part < 0);
});
document.addEventListener("click", (e) => {
  const b = e.target.closest(".ts, .ts-link");
  if (b && player.job) player.seek(Number(b.dataset.t));
});

/* ---------- Поиск по разговору ---------- */
const search = {
  hits: [], pos: -1, timer: null,
  run() {
    const q = $("#search").value.trim(), j = currentJob;
    this.hits = []; this.pos = -1;
    if (!j || !(j.utterances || []).length) return;
    const re = q.length >= 2 ? new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi") : null;
    $$(".utt").forEach((el) => {
      const text = j.utterances[el.dataset.i].text;
      const p = el.querySelector("p");
      if (re && re.test(text)) {
        re.lastIndex = 0;
        p.innerHTML = esc(text).replace(new RegExp(re.source.replace(/&/g, "&amp;"), "gi"), (m) => `<mark>${m}</mark>`);
        this.hits.push(el);
      } else if (p.querySelector("mark")) {
        p.textContent = text;
      }
      el.classList.remove("hit-current");
    });
    $("#search-count").textContent = re ? (this.hits.length ? `${this.hits.length} найдено` : "нет совпадений") : "";
  },
  next() {
    if (!this.hits.length) return;
    $(".utt.hit-current")?.classList.remove("hit-current");
    this.pos = (this.pos + 1) % this.hits.length;
    const el = this.hits[this.pos];
    el.classList.add("hit-current");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    $("#search-count").textContent = `${this.pos + 1} из ${this.hits.length}`;
  },
};
$("#search").addEventListener("input", () => {
  clearTimeout(search.timer);
  search.timer = setTimeout(() => { switchTab("transcript"); search.run(); search.next(); }, 250);
});
$("#search").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); search.next(); } });

/* ---------- Экран встречи ---------- */
function openJobView(id) {
  currentJobId = id; currentJob = null; lastReportLen = -1;
  $("#view-new").hidden = true; $("#view-job").hidden = false;
  $("#nav-new").classList.remove("active");
  $("#job-title").textContent = "Встреча"; $("#job-sub").textContent = "";
  ["#overview", "#tabs"].forEach((s) => ($(s).hidden = true));
  showError($("#job-error"), ""); showError($("#job-warn"), "");
  $("#pane-report").innerHTML = ""; $("#pane-transcript").textContent = ""; $("#search").value = "";
  transcriptSig = ""; player.audio.pause(); player.sig = ""; player.job = null; $("#player").hidden = true;
  $$("[data-text]").forEach((el) => (el.textContent = ""));
  switchTab("report");
}

function renderSteps(status) {
  const order = ["upload", "transcribing", "analyzing", "done"];
  const cur = status === "queued" ? 1 : order.indexOf(status);
  $$("#steps li").forEach((li, i) => { li.className = i < cur || status === "done" ? "done" : i === cur ? "active" : ""; });
  $("#steps").classList.toggle("finished", status === "done");
}

function renderTexts(j) {
  const texts = j.texts || {};
  $$("[data-text]").forEach((el) => {
    const t = texts[el.dataset.text];
    el.textContent = t || (ACTIVE.includes(j.status) ? "Появится, когда отчёт будет готов." : j.report ? "Раздел не найден в отчёте." : "—");
    el.classList.toggle("empty", !t);
  });
  $$("[data-copy]").forEach((b) => (b.disabled = !texts[b.dataset.copy]));
}

function renderJob(j) {
  currentJob = j;
  const roles = j.roles || {};
  const names = j.files.map((f) => f.replace(/^\d+_/, ""));
  $("#job-title").textContent = roles.client_name ? `Встреча с клиентом: ${roles.client_name}`
    : j.meta.manager_name ? `Встреча: ${j.meta.manager_name}` : names.length > 1 ? `Встреча из ${names.length} частей` : names[0] || "Встреча";
  const manager = j.meta.manager_name || roles.manager_name;
  $("#job-sub").textContent = [fmtDate(j.created), j.duration ? fmtDur(j.duration) : "", manager ? `менеджер ${manager}` : "", j.llm]
    .filter(Boolean).join(" · ");

  if (j.status === "error") {
    const failed = j.transcript ? 2 : 1;
    $$("#steps li").forEach((li, i) => (li.className = i < failed ? "done" : i === failed ? "failed" : ""));
    $("#steps").classList.remove("finished");
  } else {
    renderSteps(j.status);
  }
  $("#job-detail").textContent = ACTIVE.includes(j.status) ? j.detail + "…" : "";
  showError($("#job-error"), j.status === "error" ? j.error : "");
  showError($("#job-warn"), j.warnings.join("\n"));

  $("#tabs").hidden = !(j.transcript || j.report);
  $("#dl-report").href = `/api/jobs/${j.id}/report.md`;
  $("#dl-transcript").href = `/api/jobs/${j.id}/transcript.txt`;
  $("#dl-report").hidden = !j.report;
  $("#dl-transcript").hidden = !j.transcript;
  $("#btn-reanalyze").hidden = !j.transcript || ACTIVE.includes(j.status);
  renderTranscript(j);
  player.setup(j);

  if (j.report.length !== lastReportLen) {
    lastReportLen = j.report.length;
    $("#pane-report").innerHTML = j.report ? DOMPurify.sanitize(marked.parse(j.report)) :
      (j.transcript ? '<p class="sub">Расшифровка готова, модель формирует отчёт…</p>' : "");
    linkifyTimestamps($("#pane-report"));
    if (j.status !== "analyzing") renderOverview(j);
  }
  if (j.status === "done") renderOverview(j);
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
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("#pane-report").hidden = name !== "report";
  $("#pane-texts").hidden = name !== "texts";
  $("#pane-transcript").hidden = name !== "transcript";
}
$$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

$$("[data-copy]").forEach((btn) => btn.addEventListener("click", async () => {
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
  else if (!rec.mr) {
    currentJobId = null; currentJob = null; player.audio.pause();
    $("#view-job").hidden = true; $("#view-new").hidden = false; $("#nav-new").classList.add("active");
  }
  loadHistory();
}
window.addEventListener("hashchange", route);
loadConfig();
route();
