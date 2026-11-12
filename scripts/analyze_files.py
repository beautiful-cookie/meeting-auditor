"""Анализ встречи из командной строки, без веб-интерфейса.

    python scripts/analyze_files.py часть1.m4a [часть2.m4a ...] [--manager "Иван"] [--date 2026-11-10]
    python scripts/analyze_files.py --reanalyze <id задачи>     # повторить только анализ

Несколько файлов считаются частями одной встречи и склеиваются по порядку.
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, jobs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--manager", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--reanalyze", metavar="JOB_ID")
    ap.add_argument("--model", help="переопределить модель из .env для этого запуска")
    ap.add_argument("--reroles", action="store_true", help="заново определить роли спикеров")
    a = ap.parse_args()
    if a.model:
        config.OPENAI_MODEL = config.ANTHROPIC_MODEL = a.model

    if a.reanalyze:
        job = jobs.get(a.reanalyze)
        if not job:
            print("Задача не найдена")
            return 1
        if a.reroles:
            job.roles = {}
        jobs.run(job, skip_stt=True, log=print)
    else:
        if not a.files:
            ap.error("укажите хотя бы один аудиофайл")
        job = jobs.new({"manager_name": a.manager, "meeting_date": a.date})
        for i, f in enumerate(a.files, start=1):
            src = Path(f)
            name = f"{i:02d}_{src.name.replace(' ', '_')}"
            shutil.copy(src, jobs.job_dir(job.id) / name)
            job.files.append(name)
        print(f"Задача {job.id}")
        jobs.run(job, log=print)

    d = jobs.job_dir(job.id)
    for w in job.warnings:
        print("ВНИМАНИЕ:", w)
    if job.status == "error":
        print("ОШИБКА:", job.error)
        return 1
    print(f"Длительность: {job.duration / 60:.1f} мин; модель: {job.llm}")
    print(f"Транскрипт: {d / 'transcript.txt'}\nОтчёт:      {d / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
