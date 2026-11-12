"""Сборка промпта заказчика: части I–VII — системное сообщение, часть VIII с данными встречи — пользовательское."""
from . import config

SPLIT_MARK = "# **ЧАСТЬ VIII"
FIELDS = ["MANAGER_NAME", "MANAGER_ID", "MEETING_DATE", "MEETING_ID",
          "CRM_DATA", "CLIENT_HISTORY", "COMPANY_RULES", "PRODUCT_KNOWLEDGE"]
EMPTY = "не предоставлено"

TRANSCRIPT_NOTE = (
    "Формат расшифровки: [чч:мм:сс] Участник: реплика. Голоса разделены системой распознавания речи, "
    "роли (Менеджер, Клиент и т. д.) расставлены автоматически по контексту и могут ошибаться: если "
    "реплика явно принадлежит другому участнику, учитывай это. Если вместо ролей стоят метки «Спикер X», "
    "определи роли сам. Отдельные слова могут быть распознаны с ошибками. "
    "Ссылаясь на фрагмент разговора, указывай таймкод в формате [чч:мм:сс].\n\n"
)


def build(transcript: str, meta: dict) -> tuple[str, str]:
    tpl = config.PROMPT_PATH.read_text(encoding="utf-8")
    i = tpl.find(SPLIT_MARK)
    if i > 0:
        system, user = tpl[:i].strip(), tpl[i:].strip()
    else:
        system, user = tpl.split("{{TRANSCRIPT}}")[0].strip(), "{{TRANSCRIPT}}"

    if config.STYLE_ADDENDUM_PATH.exists():
        system += "\n\n-----\n\n" + config.STYLE_ADDENDUM_PATH.read_text(encoding="utf-8").strip()

    for f in FIELDS:
        user = user.replace("{{" + f + "}}", (meta.get(f.lower()) or "").strip() or EMPTY)
    user = user.replace("{{TRANSCRIPT}}", TRANSCRIPT_NOTE + transcript)
    return system, user
