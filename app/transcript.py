"""Склейка частей встречи, текстовый вид транскрипта и поведенческие метрики разговора."""


def fmt_ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def merge(parts: list[dict]) -> tuple[list[dict], float]:
    """Части одной встречи идут подряд: таймкоды каждой следующей сдвигаются на длительность предыдущих."""
    offset, out = 0.0, []
    for n, part in enumerate(parts, start=1):
        for u in part["utterances"]:
            out.append({**u, "start": u["start"] + offset, "end": u["end"] + offset, "part": n})
        offset += part["duration"]
    return out, offset


def speaker_label(u: dict, n_parts: int) -> str:
    return f"Спикер {u['speaker']}" if n_parts == 1 else f"Спикер {u['speaker']} (ч. {u['part']})"


def to_text(utts: list[dict], file_names: list[str]) -> str:
    n = len(file_names)
    lines, cur = [], None
    for u in utts:
        if n > 1 and u["part"] != cur:
            cur = u["part"]
            lines.append(f"--- Часть {cur} из {n} (файл «{file_names[cur - 1]}»). "
                         f"Буквы спикеров в каждой части назначены заново ---")
        lines.append(f"[{fmt_ts(u['start'])}] Спикер {u['speaker']}: {u['text']}")
    return "\n".join(lines)


def metrics(utts: list[dict], n_parts: int) -> list[dict]:
    """Объективные показатели без ИИ: доля речи, число реплик и вопросов, самый длинный монолог."""
    stats: dict[str, dict] = {}
    total = sum(max(0.0, u["end"] - u["start"]) for u in utts) or 1.0
    for u in utts:
        key = speaker_label(u, n_parts)
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
