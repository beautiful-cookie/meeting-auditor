"""Анализ расшифровки языковой моделью. Провайдер переключается одной переменной LLM_PROVIDER в .env."""
import json
import re
from typing import Iterator

from . import config


class LLMError(Exception):
    pass


def describe() -> str:
    if config.LLM_PROVIDER == "anthropic":
        return f"anthropic · {config.ANTHROPIC_MODEL}"
    host = config.OPENAI_BASE_URL.split("//")[-1].split("/")[0] if config.OPENAI_BASE_URL else "openai"
    return f"{host} · {config.OPENAI_MODEL}"


def ready() -> bool:
    if config.LLM_PROVIDER == "anthropic":
        return bool(config.ANTHROPIC_API_KEY)
    return bool(config.OPENAI_API_KEY) or bool(config.OPENAI_BASE_URL)


def stream(system: str, user: str, max_tokens: int | None = None) -> Iterator[tuple[str, str]]:
    """Отдаёт ("text", кусок) по мере генерации и в конце ("finish", "max_tokens" | "stop" | …)."""
    max_tokens = max_tokens or config.LLM_MAX_TOKENS
    if config.LLM_PROVIDER == "anthropic":
        yield from _anthropic(system, user, max_tokens)
    elif config.LLM_PROVIDER == "openai":
        yield from _openai(system, user, max_tokens)
    else:
        raise LLMError(f"Неизвестный LLM_PROVIDER: {config.LLM_PROVIDER}")


def complete(system: str, user: str, max_tokens: int = 2000) -> str:
    """Короткий вызов без стриминга (роли спикеров, редактура текстов)."""
    return "".join(v for k, v in stream(system, user, max_tokens) if k == "text")


def complete_json(system: str, user: str, max_tokens: int = 2000) -> dict:
    """Вызов, ответ которого — JSON-объект. Модель иногда оборачивает его в ```json — снимаем."""
    text = complete(system, user, max_tokens)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMError(f"Модель не вернула JSON: {text[:200]}")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMError(f"Некорректный JSON от модели: {e}") from e


def _openai(system: str, user: str, max_tokens: int) -> Iterator[tuple[str, str]]:
    from openai import OpenAI, OpenAIError

    client = OpenAI(api_key=config.OPENAI_API_KEY or "not-needed", base_url=config.OPENAI_BASE_URL or None)
    # Официальный API принимает max_completion_tokens (в т. ч. для reasoning-моделей),
    # большинство совместимых провайдеров — классический max_tokens.
    limit = ({"max_tokens": max_tokens} if config.OPENAI_BASE_URL
             else {"max_completion_tokens": max_tokens})
    finish = None
    try:
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            stream=True,
            **limit,
        )
        for ev in resp:
            if not ev.choices:
                continue
            ch = ev.choices[0]
            if ch.delta and ch.delta.content:
                yield "text", ch.delta.content
            if ch.finish_reason:
                finish = ch.finish_reason
    except OpenAIError as e:
        raise LLMError(f"Ошибка LLM ({describe()}): {e}") from e
    yield "finish", "max_tokens" if finish == "length" else (finish or "stop")


def _anthropic(system: str, user: str, max_tokens: int) -> Iterator[tuple[str, str]]:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, base_url=config.ANTHROPIC_API_BASE)
    try:
        with client.messages.stream(
            model=config.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as s:
            for text in s.text_stream:
                yield "text", text
            final = s.get_final_message()
    except anthropic.AnthropicError as e:
        raise LLMError(f"Ошибка LLM ({describe()}): {e}") from e
    yield "finish", final.stop_reason or "stop"
