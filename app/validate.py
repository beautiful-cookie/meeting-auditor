"""Проверка отчёта модели по правилам промпта: баллы в пределах максимума, сумма, наличие всех 13 разделов.

Модель может ошибиться в арифметике или пропустить раздел — такие места помечаются предупреждениями,
чтобы руководитель не принял ошибку модели за оценку менеджера.
"""
import re

STAGE_MAX = {1: 8, 2: 20, 3: 5, 4: 15, 5: 10, 6: 10, 7: 15, 8: 10, 9: 7}
STAGE_TITLES = {1: "Контакт", 2: "Потребности", 3: "Сравнение", 4: "Презентация", 5: "Эмоции",
                6: "Цена", 7: "Закрытие", 8: "Возражения", 9: "Следующий шаг"}  # короткие — подписи под столбиками
NUM = r"(\d+(?:[.,]\d+)?)"


def name_heard(name: str, transcript: str) -> bool:
    """Звучало ли имя в разговоре (с учётом падежа и ё/е): «Алёна» найдёт «Алену», но «Лиля» не найдёт «утеплили»."""
    norm = lambda s: s.lower().replace("ё", "е")  # noqa: E731
    stem = re.escape(norm(name)[:-1] if len(name) > 3 else norm(name))
    return re.search(rf"\b{stem}[а-я]{{0,3}}\b", norm(transcript)) is not None


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def stage_table(report: str) -> list[dict]:
    """Баллы по девяти этапам из таблицы оценки: [{stage, title, got, max, comment}]; got=None — Н/П или нет данных."""
    found: dict[int, dict] = {}
    for line in report.splitlines():
        cells = [c.strip(" *") for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        m = re.match(r"^(\d)\b\.?\s*(.*)$", cells[0])
        if not m or int(m.group(1)) not in STAGE_MAX or int(m.group(1)) in found:
            continue
        stage = int(m.group(1))
        rest = cells[1:]
        if not m.group(2).strip() and rest and not re.fullmatch(NUM, rest[0]):
            rest = rest[1:]  # таблица вида «| 1 | Приветствие | 6 | 8 |»
        nums = [c for c in rest[:3] if re.fullmatch(NUM, c)]
        texts = [c for c in rest if c and not re.fullmatch(NUM, c)]
        found[stage] = {"stage": stage, "title": STAGE_TITLES[stage], "max": STAGE_MAX[stage],
                        "got": _num(nums[0]) if nums else None,
                        "comment": texts[0][:200] if texts else ""}
    return [found[s] for s in sorted(found)]


def total_score(report: str) -> float | None:
    m = re.search(r"Итог[^0-9\n]{0,25}" + NUM + r"\s*/\s*100", report)
    return _num(m.group(1)) if m else None


def check(report: str, transcript: str = "") -> list[str]:
    issues: list[str] = []

    # Имя менеджера в карточке встречи, которого нет в записи, — выдумка модели
    m = re.search(r"Менеджер\W{0,6}\s*([А-ЯЁ][а-яё]{2,})", report)
    if transcript and m and m.group(1) not in ("Не", "Нет", "Неизвестно", "Имя"):
        if not name_heard(m.group(1), transcript):
            issues.append(f"В карточке встречи менеджер назван «{m.group(1)}», но этого имени нет в записи.")

    scores = {s["stage"]: s["got"] for s in stage_table(report) if s["got"] is not None}
    for stage, got in scores.items():
        if got > STAGE_MAX[stage]:
            issues.append(f"Этап {stage}: {got:g} баллов при максимуме {STAGE_MAX[stage]} — ошибка модели.")

    total = re.search(r"Итог[^0-9\n]{0,25}" + NUM + r"\s*/\s*100", report)
    if scores and total:
        possible = sum(STAGE_MAX[s] for s in scores)
        expected = sum(scores.values()) / possible * 100
        if abs(expected - _num(total.group(1))) > 1.5:
            issues.append(f"Итог {total.group(1)}/100 не сходится с баллами этапов "
                          f"({sum(scores.values()):g} из {possible} = {expected:.0f}/100).")

    missing = [n for n in range(1, 14) if not re.search(rf"^#+\s*\**\s*{n}\.", report, re.M)]
    if missing:
        issues.append("В отчёте нет разделов: " + ", ".join(map(str, missing)) + ".")
    if not re.search(r"[Пп]оследовательность презентации цены", report):
        issues.append("Нет вывода о последовательности презентации цены (обязателен по регламенту).")
    return issues
