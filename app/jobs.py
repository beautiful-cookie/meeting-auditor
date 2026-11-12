"""Задача «встреча»: файлы → распознавание → анализ. Состояние в памяти, копия на диске в data/<id>/."""
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import client_texts, config, llm, prompt, stt, transcript, validate

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
            job.utterances, job.duration = transcript.merge(parts)
            job.transcript = transcript.to_text(job.utterances, job.files)
            job.metrics = transcript.metrics(job.utterances, n)
            if not job.transcript.strip():
                raise RuntimeError("Речь в записи не распознана — проверьте, что в файле есть звук.")

        job.warnings = [w for w in job.warnings if not w.startswith(("Проверка отчёта:", "Отчёт обрезан"))]
        job.report, job.texts, job.error, job.llm = "", {}, "", llm.describe()
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
        job.warnings += ["Проверка отчёта: " + w
                         for w in validate.check(job.report) + client_texts.check(job.texts)]
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
