"""Распознавание речи через AssemblyAI REST API.

Файл любой длины загружается целиком (у AssemblyAI нет лимита 25 МБ, как у Whisper API),
включается разделение по спикерам, на выходе — реплики с таймкодами в секундах.
"""
import re
import time
from pathlib import Path
from typing import Callable

import httpx

from . import config

POLL_INTERVAL = 3
TIMEOUT_SEC = 3 * 3600


class STTError(Exception):
    pass


def ready() -> bool:
    return bool(config.ASSEMBLYAI_API_KEY)


def _client() -> httpx.Client:
    if not config.ASSEMBLYAI_API_KEY:
        raise STTError("Не задан ASSEMBLYAI_API_KEY в файле .env")
    return httpx.Client(
        base_url=config.ASSEMBLYAI_BASE_URL,
        headers={"authorization": config.ASSEMBLYAI_API_KEY},
        timeout=httpx.Timeout(60, read=600),
    )


def _check(r: httpx.Response) -> None:
    if r.status_code >= 400:
        try:
            msg = r.json().get("error") or r.text
        except Exception:
            msg = r.text
        raise STTError(f"AssemblyAI {r.status_code}: {msg}")


def _upload(c: httpx.Client, path: Path) -> str:
    def chunks():
        with open(path, "rb") as f:
            while chunk := f.read(5 * 1024 * 1024):
                yield chunk

    r = c.post("/v2/upload", content=chunks(), headers={"content-type": "application/octet-stream"})
    _check(r)
    return r.json()["upload_url"]


def _run_transcript(c: httpx.Client, audio_url: str, diarize: bool) -> dict:
    body = {
        "audio_url": audio_url,
        "language_code": config.ASSEMBLYAI_LANGUAGE,
        "speech_models": [config.ASSEMBLYAI_SPEECH_MODEL],
        "punctuate": True,
        "format_text": True,
    }
    if diarize:
        body["speaker_labels"] = True
    r = c.post("/v2/transcript", json=body)
    _check(r)
    tid = r.json()["id"]

    started = time.time()
    while True:
        r = c.get(f"/v2/transcript/{tid}")
        _check(r)
        data = r.json()
        if data["status"] == "completed":
            return data
        if data["status"] == "error":
            raise STTError(f"AssemblyAI: {data.get('error')}")
        if time.time() - started > TIMEOUT_SEC:
            raise STTError("AssemblyAI: превышено время ожидания распознавания")
        time.sleep(POLL_INTERVAL)


def _sentences_from_words(words: list[dict]) -> list[dict]:
    """Запасной вариант без диаризации: режем текст на предложения, чтобы сохранить таймкоды."""
    out, buf = [], []
    for w in words:
        buf.append(w)
        if re.search(r"[.!?…]$", w["text"]) and len(buf) >= 4:
            out.append(buf)
            buf = []
    if buf:
        out.append(buf)
    return [
        {"speaker": "?", "start": b[0]["start"] / 1000, "end": b[-1]["end"] / 1000,
         "text": " ".join(w["text"] for w in b)}
        for b in out
    ]


def transcribe(path: Path, log: Callable[[str], None] = lambda m: None) -> dict:
    """Возвращает {utterances:[{speaker,start,end,text}], duration, model, warnings}."""
    warnings: list[str] = []
    with _client() as c:
        log("загрузка файла в сервис распознавания")
        audio_url = _upload(c, path)
        log("распознавание речи и разделение по спикерам")
        try:
            data = _run_transcript(c, audio_url, diarize=True)
        except STTError as e:
            if "speaker_labels" not in str(e):
                raise
            warnings.append("Разделение по спикерам недоступно для выбранного языка/модели — "
                            "транскрипт без меток спикеров.")
            data = _run_transcript(c, audio_url, diarize=False)

    utts = data.get("utterances") or []
    if utts:
        items = [{"speaker": u["speaker"], "start": u["start"] / 1000, "end": u["end"] / 1000,
                  "text": (u.get("text") or "").strip()} for u in utts]
    elif data.get("words"):
        items = _sentences_from_words(data["words"])
    else:
        items = [{"speaker": "?", "start": 0, "end": 0, "text": (data.get("text") or "").strip()}]

    duration = data.get("audio_duration") or (items[-1]["end"] if items else 0)
    return {"utterances": [i for i in items if i["text"]], "duration": float(duration),
            "model": data.get("speech_model_used") or config.ASSEMBLYAI_SPEECH_MODEL,
            "warnings": warnings}
