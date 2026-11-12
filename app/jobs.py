"""Задача «встреча»: файлы → распознавание → анализ. Состояние в памяти, копия на диске в data/<id>/."""
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import client_texts, config, editor, llm, prompt, roles, stt, transcript, validate

ACTIVE = ("queued", "transcribing", "analyzing")


@dataclass
class Job:
    id: str
    created: float
    meta: dict
    files: list[str] = field(default_factory=list)  # имена файлов внутри папки задачи
    status: str = "queued"  # queued | transcribing | analyzing | done | error
    detail: str = ""
    duration: float = 0.0
    stt_model: str = ""
    llm: str = ""
    transcript: str = ""
    utterances: list[dict] = field(default_factory=list)
    parts: list[dict] = field(default_factory=list)  # [{file, offset, duration}] — для плеера
    roles: dict = field(default_factory=dict)  # {"roles": {"A": "Менеджер"}, "manager_name", "manager_gender", …}
    metrics: list[dict] = field(default_factory=list)
    report: str = ""
    texts: dict = field(default_factory=dict)  # call_script, client_message, crm_summary
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    finished: float | None = None

    def summary(self) -> dict:
        return {"id": self.id, "created": self.created, "status": self.status, "files": self.files,
                "duration": self.duration, "manager_name": self.meta.get("manager_name", "")}


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def job_dir(job_id: str) -> Path:
    return config.DATA_DIR / job_id


def save(job: Job) -> None:
    d = job_dir(job.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.json").write_text(json.dumps(asdict(job), ensure_ascii=False, indent=1), encoding="utf-8")
    if job.transcript:
        (d / "transcript.txt").write_text(job.transcript, encoding="utf-8")
    if job.report:
        (d / "report.md").write_text(job.report, encoding="utf-8")


def _load_all() -> None:
    for p in sorted(config.DATA_DIR.glob("*/job.json")):
        try:
            job = Job(**json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
        if job.status in ACTIVE:
            job.status, job.error = "error", "Обработка прервана перезапуском сервера — запустите анализ повторно."
        _jobs[job.id] = job


def new(meta: dict) -> Job:
    job = Job(id=time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6], created=time.time(), meta=meta)
    with _lock:
        _jobs[job.id] = job
    job_dir(job.id).mkdir(parents=True, exist_ok=True)
    return job


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def all_jobs() -> list[Job]:
    return sorted(_jobs.values(), key=lambda j: -j.created)


def run(job: Job, skip_stt: bool = False, log: Callable[[str], None] = lambda m: None) -> Job:
    def step(status: str, detail: str) -> None:
        job.status, job.detail = status, detail
        log(detail)
        save(job)

    try:
        if not skip_stt:
            job.warnings = []
            parts, n = [], len(job.files)
            for i, name in enumerate(job.files, start=1):
                prefix = f"Файл {i} из {n}: " if n > 1 else ""
                step("transcribing", prefix + "загрузка и распознавание речи")
                part = stt.transcribe(job_dir(job.id) / name,
                                      log=lambda m, p=prefix: step("transcribing", p + m))
                job.warnings += [w for w in part["warnings"] if w not in job.warnings]
                job.stt_model = part["model"]
                parts.append(part)
            job.utterances, job.parts, job.duration = transcript.merge(parts, job.files)
            job.roles = {}
            if not any(u["text"].strip() for u in job.utterances):
                raise RuntimeError("Речь в записи не распознана — проверьте, что в файле есть звук.")
        elif not job.parts and len(job.files) == 1:  # встречи, распознанные до появления плеера
            job.parts = [{"file": job.files[0], "offset": 0.0, "duration": job.duration}]

        job.warnings = [w for w in job.warnings
                        if not w.startswith(("Проверка отчёта:", "Отчёт обрезан", "Роли спикеров", "Редактор"))]
        job.report, job.texts, job.error, job.llm = "", {}, "", llm.describe()
        n = len(job.files)

        if not job.roles and job.utterances:
            step("analyzing", "Определение ролей спикеров")
            try:
                job.roles = roles.detect(job.utterances, n)
            except Exception as e:  # noqa: BLE001 — без ролей анализ всё равно возможен
                job.warnings.append(f"Роли спикеров не определены: {e}")
        if job.utterances:
            job.transcript = transcript.to_text(job.utterances, job.files, job.roles.get("roles"))
            job.metrics = transcript.metrics(job.utterances, n, job.roles.get("roles"))

        step("analyzing", f"Анализ встречи ({job.llm})")
        system, user = prompt.build(job.transcript, job.meta)
        (job_dir(job.id) / "prompt_user.txt").write_text(user, encoding="utf-8")

        last_save = time.time()
        for kind, value in llm.stream(system, user):
            if kind == "text":
                job.report += value
                if time.time() - last_save > 3:
                    save(job)
                    last_save = time.time()
            elif kind == "finish" and value == "max_tokens":
                job.warnings.append(f"Отчёт обрезан лимитом длины ответа ({config.LLM_MAX_TOKENS} токенов). "
                                    "Увеличьте LLM_MAX_TOKENS или выберите модель с большим лимитом.")
        job.texts = client_texts.extract(job.report)

        step("analyzing", "Редактура текстов для клиента")
        try:
            edited = editor.polish(job.texts, job.report, job.roles, job.meta)
            job.texts.update(edited)
            for key, num in (("call_script", 9), ("client_message", 10)):
                if key in edited:  # в markdown одиночный перенос строки склеивается — делаем абзацы
                    body = "\n\n".join(l for l in edited[key].splitlines() if l.strip())
                    job.report = client_texts.replace_section(job.report, num, body)
        except Exception as e:  # noqa: BLE001 — черновик из отчёта остаётся
            job.warnings.append(f"Редактор не сработал, показаны черновики из отчёта: {e}")

        job.warnings += ["Проверка отчёта: " + w
                         for w in validate.check(job.report, job.transcript)
                         + client_texts.check(job.texts, job.roles.get("manager_gender") if job.roles else "unknown",
                                              job.transcript)]
        job.status, job.detail = "done", "Готово"
        log("готово")
    except Exception as e:  # noqa: BLE001 — любую ошибку показываем пользователю
        job.status, job.error = "error", str(e)
        log(f"ошибка: {e}")
    finally:
        job.finished = time.time()
        save(job)
    return job


def start(job: Job, skip_stt: bool = False) -> None:
    threading.Thread(target=run, args=(job, skip_stt), daemon=True).start()


_load_all()
