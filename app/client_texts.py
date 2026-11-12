"""Готовые тексты из отчёта: резюме для CRM, скрипт звонка, сообщение клиенту.

Тексты для клиента дополнительно чистятся кодом от следов генерации: тире, markdown, кавычки-«цитаты»,
слипшиеся суммы. Модель получает правила стиля в промпте, а этот слой страхует от того, что она их нарушит.
"""
import re

SECTIONS = {
    "call_script": 9,
    "client_message": 10,
    "crm_summary": 12,
}
CLIENT_FACING = ("call_script", "client_message")

PLACEHOLDER = re.compile(r"\[[^\]\n]{2,40}\]|\b[NXY]\s+(?:панел|тыс|руб|штук)|ХХ")


def section(report: str, number: int) -> str:
    """Текст раздела «## N. …» до следующего заголовка того же или более высокого уровня."""
    lines = report.splitlines()
    start = next((i for i, l in enumerate(lines)
                  if re.match(rf"^(#+)\s*\**\s*{number}\.\s", l)), None)
    if start is None:
        return ""
    level = len(re.match(r"^#+", lines[start]).group(0))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        h = re.match(r"^(#+)\s", lines[i])
        if h and len(h.group(1)) <= level:
            end = i
            break
    return "\n".join(lines[start + 1:end]).strip()


def strip_markdown(text: str) -> str:
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)          # цитаты
    text = re.sub(r"^\s*-{3,}\s*$", "", text, flags=re.M)      # горизонтальные линии
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.M)
    return text


def humanize(text: str) -> str:
    """Убирает типографские следы генерации из текста, который уйдёт клиенту как есть."""
    text = strip_markdown(text)
    # Весь текст скрипта/сообщения в кавычках целиком — снимаем
    text = re.sub(r'^\s*[«"](.+)[»"]\s*$', r"\1", text, flags=re.S)
    # Тире. Это страховка: модель и так просят обходиться без них.
    text = re.sub(r"^\s*[—–]\s*", "", text, flags=re.M)              # в начале реплики
    text = re.sub(r"(\d)\s*[—–]\s*(\d)", r"\1-\2", text)              # диапазон 10–15
    text = re.sub(r"\s+[—–]\s+(?=\d|это\b)", " ", text)               # «полуторка — 291 200», «— это»
    text = re.sub(r"\s+[—–]\s+", ", ", text)                          # остальное — запятая
    text = text.replace("—", "-").replace("–", "-")
    text = re.sub(r",\s*([,.!?:])", r"\1", text)
    # Кавычки вокруг коротких названий: "Караэт" → Караэт
    text = re.sub(r'["«]([^"«»\n]{2,30})["»]', r"\1", text)
    # Суммы: 322240₽ / 322 240₽ → 322 240 ₽
    text = re.sub(r"(\d)\s*₽", r"\1 ₽", text)
    text = re.sub(r"(?<!\d)(\d{5,})(?=\s*(?:₽|руб))",
                  lambda m: f"{int(m.group(1)):,}".replace(",", " "), text)
    text = text.replace("…", "...")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract(report: str) -> dict:
    out = {}
    for key, num in SECTIONS.items():
        raw = section(report, num)
        out[key] = humanize(raw) if key in CLIENT_FACING else strip_markdown(raw).strip()
    return out


def check(texts: dict) -> list[str]:
    issues = []
    names = {"call_script": "Скрипт звонка", "client_message": "Сообщение клиенту"}
    for key, title in names.items():
        t = texts.get(key, "")
        if not t:
            issues.append(f"{title}: раздел не найден в отчёте.")
        elif m := PLACEHOLDER.search(t):
            issues.append(f"{title}: осталась заготовка «{m.group(0)}» — заполните перед отправкой.")
        elif m := re.search(r"\b[А-Яа-яЁё]+/[А-Яа-яЁё]+\b", t):
            issues.append(f"{title}: вариант через косую черту «{m.group(0)}» — выберите один.")
        if m := re.search(r"\b(вчера|сегодня|завтра|позавчера)\b", t, re.I):
            issues.append(f"{title}: «{m.group(0)}» — проверьте, что это верно на момент отправки.")
    return issues
