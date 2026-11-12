"""Роли спикеров: «Спикер A» → «Менеджер», «Спикер B» → «Клиент».

Распознавание делит запись на голоса, но не знает, кто есть кто. Короткий вызов модели по началу
разговора и выборке реплик каждого голоса определяет роли, имя и грамматический род менеджера —
это нужно и для читаемой расшифровки, и для текстов клиенту от лица менеджера.
"""
import re

from . import llm, transcript

FULL_LIMIT = 80_000  # символов; ~час разговора — дешевле и надёжнее отдать расшифровку целиком
NAME = re.compile(r"(?<=[\w,] )[А-ЯЁ][а-яё]{2,}\b")
PAST = re.compile(r"\b(я|сама|сам)\b.*\b\w{2,}(л|ла|лся|лась)\b|\b\w{3,}(ила|ала|яла|ела|лась)\b", re.I)

SYSTEM = """Ты размечаешь расшифровку встречи менеджера по продажам с клиентом в офисе компании.
Голоса уже разделены системой распознавания и помечены буквами. Определи роль каждого голоса.

Верни только JSON-объект:
{
  "roles": {"<метка>": "<роль>", ...},
  "manager_name": "<имя менеджера, если он представился или его назвали, иначе null>",
  "manager_gender": "f" | "m" | null,
  "client_name": "<имя основного клиента, если прозвучало, иначе null>",
  "confidence": "high" | "medium" | "low"
}

Правила:
- Метки — ровно те, что даны во входных данных (например "A" или "2:B").
- Роль — коротко по-русски, с заглавной: «Менеджер», «Клиент», «Супруга клиента», «Второй менеджер»,
  «Коллега менеджера». Роли должны различаться: если два клиента, назови их по-разному.
- Менеджер обычно задаёт вопросы об объекте, рассказывает о продукции, считает стоимость.
- manager_gender определяй по грамматике речи самого менеджера о себе: «я посчитала», «выставила счёт»,
  «я сама покажу» → "f"; «я посчитал», «я сам покажу» → "m". Смотри «Фразы с глаголами прошедшего времени»:
  глагол без другого подлежащего обычно относится к самому говорящему. Если оснований нет — null.
- manager_name — только имя, которое реально прозвучало в разговоре: менеджер представился («меня зовут…»,
  «это я, Лиля», «звоните мне, Лиля») или к нему обратились по имени. Смотри «Фразы с именами». Не угадывай.
- Если голос сказал одно-два слова и роль неясна, всё равно выбери наиболее вероятную."""


def _key(u: dict, n_parts: int) -> str:
    return u["speaker"] if n_parts == 1 else f"{u['part']}:{u['speaker']}"


def detect(utts: list[dict], n_parts: int) -> dict:
    by_speaker: dict[str, list[dict]] = {}
    for u in utts:
        by_speaker.setdefault(_key(u, n_parts), []).append(u)

    head = "\n".join(f"[{transcript.fmt_ts(u['start'])}] {_key(u, n_parts)}: {u['text'][:220]}"
                     for u in utts[:45])
    samples = []
    for key, items in by_speaker.items():
        talk = sum(u["end"] - u["start"] for u in items)
        longest = sorted(items, key=lambda u: -len(u["text"]))[:6]
        lines = "\n".join(f"  — {u['text'][:260]}" for u in longest)
        # Фразы с глаголами прошедшего времени — по ним видно, как говорящий говорит о себе («я выставила»)
        past = [s.strip() for u in items for s in re.split(r"(?<=[.!?])\s+", u["text"])
                if PAST.search(s)][:12]
        past_lines = "\n".join(f"  · {s[:200]}" for s in past)
        # Фразы с именами собственными не в начале предложения — так находятся «я на этом телефоне, Лиля»
        named = [s.strip() for u in items for s in re.split(r"(?<=[.!?])\s+", u["text"])
                 if NAME.search(s) and "Royal" not in s][:12]
        named_lines = "\n".join(f"  · {s[:200]}" for s in named)
        samples.append(f"Голос {key}: {len(items)} реплик, {round(talk)} с речи. Самые длинные реплики:\n{lines}"
                       + (f"\nФразы с глаголами прошедшего времени:\n{past_lines}" if past else "")
                       + (f"\nФразы с именами:\n{named_lines}" if named else ""))

    full = "\n".join(f"[{transcript.fmt_ts(u['start'])}] {_key(u, n_parts)}: {u['text']}" for u in utts)
    if len(full) <= FULL_LIMIT:
        # Целиком надёжнее: имя может прозвучать один раз в конце встречи («я на этом телефоне, Лиля»)
        user = f"Расшифровка целиком:\n{full}\n\nСтатистика по голосам:\n\n" + "\n\n".join(samples)
    else:
        user = f"Начало разговора:\n{head}\n\nВыборка по голосам:\n\n" + "\n\n".join(samples)
    user += f"\n\nМетки: {', '.join(by_speaker)}"
    data = llm.complete_json(SYSTEM, user, max_tokens=600)

    roles = {k: str(v).strip() for k, v in (data.get("roles") or {}).items() if k in by_speaker and v}
    # Роли должны различаться — иначе метрики и расшифровка склеят разных людей
    seen: dict[str, int] = {}
    for k in sorted(roles, key=lambda k: -len(by_speaker[k])):
        seen[roles[k]] = seen.get(roles[k], 0) + 1
        if seen[roles[k]] > 1:
            roles[k] = f"{roles[k]} ({k})"
    gender = data.get("manager_gender")
    return {
        "roles": roles,
        "manager_name": data.get("manager_name") or None,
        "manager_gender": gender if gender in ("f", "m") else None,
        "client_name": data.get("client_name") or None,
        "confidence": data.get("confidence") or None,
    }
