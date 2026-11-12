"""Проверка отчёта модели по правилам промпта: баллы в пределах максимума, сумма, наличие всех 13 разделов.

Модель может ошибиться в арифметике или пропустить раздел — такие места помечаются предупреждениями,
чтобы руководитель не принял ошибку модели за оценку менеджера.
"""
import re

STAGE_MAX = {1: 8, 2: 20, 3: 5, 4: 15, 5: 10, 6: 10, 7: 15, 8: 10, 9: 7}
NUM = r"(\d+(?:[.,]\d+)?)"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def check(report: str) -> list[str]:
    issues: list[str] = []
    scores: dict[int, float] = {}

    for line in report.splitlines():
        cells = [c.strip(" *") for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        m = re.match(r"^(\d)\b", cells[0])
        if not m or int(m.group(1)) not in STAGE_MAX or int(m.group(1)) in scores:
            continue
        stage = int(m.group(1))
        nums = [c for c in cells[1:4] if re.fullmatch(NUM, c)]
        if not nums:
            continue  # «Н/П» или «Нет данных»
        got = _num(nums[0])
        scores[stage] = got
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
