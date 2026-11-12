"""Склейка частей встречи, текстовый вид транскрипта и поведенческие метрики разговора."""


def fmt_ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def merge(parts: list[dict], files: list[str]) -> tuple[list[dict], list[dict], float]:
    """Части одной встречи идут подряд: таймкоды каждой следующей сдвигаются на длительность предыдущих.

    Возвращает реплики, раскладку частей [{file, offset, duration}] для плеера и общую длительность.
    """
    offset, out, layout = 0.0, [], []
    for n, (part, name) in enumerate(zip(parts, files), start=1):
        for u in part["utterances"]:
            out.append({**u, "start": u["start"] + offset, "end": u["end"] + offset, "part": n})
        layout.append({"file": name, "offset": offset, "duration": part["duration"]})
        offset += part["duration"]
    return out, layout, offset


def speaker_key(u: dict, n_parts: int) -> str:
    return u["speaker"] if n_parts == 1 else f"{u['part']}:{u['speaker']}"


def label(u: dict, n_parts: int, roles: dict | None = None) -> str:
    key = speaker_key(u, n_parts)
    if roles and key in roles:
        return roles[key]
    return f"Спикер {u['speaker']}" if n_parts == 1 else f"Спикер {u['speaker']} (ч. {u['part']})"


def to_text(utts: list[dict], file_names: list[str], roles: dict | None = None) -> str:
    n = len(file_names)
    lines, cur = [], None
    for u in utts:
        if n > 1 and u["part"] != cur:
            cur = u["part"]
            note = "" if roles else ". Буквы спикеров в каждой части назначены заново"
            lines.append(f"--- Часть {cur} из {n} (файл «{file_names[cur - 1]}»){note} ---")
        lines.append(f"[{fmt_ts(u['start'])}] {label(u, n, roles)}: {u['text']}")
    return "\n".join(lines)


def metrics(utts: list[dict], n_parts: int, roles: dict | None = None) -> list[dict]:
    """Объективные показатели без ИИ: доля речи, число реплик и вопросов, самый длинный монолог."""
    stats: dict[str, dict] = {}
    total = sum(max(0.0, u["end"] - u["start"]) for u in utts) or 1.0
    for u in utts:
        key = label(u, n_parts, roles)
        st = stats.setdefault(key, {"speaker": key, "talk_sec": 0.0, "turns": 0,
                                    "questions": 0, "longest_sec": 0.0})
        d = max(0.0, u["end"] - u["start"])
        st["talk_sec"] += d
        st["turns"] += 1
        st["questions"] += u["text"].count("?")
        st["longest_sec"] = max(st["longest_sec"], d)
    for st in stats.values():
        st["share"] = round(st["talk_sec"] / total * 100, 1)
        st["talk_sec"] = round(st["talk_sec"])
        st["longest_sec"] = round(st["longest_sec"])
    return sorted(stats.values(), key=lambda s: -s["talk_sec"])
