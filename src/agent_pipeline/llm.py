"""One JSON completion client. Callers parse the dict into a schema they own."""

from __future__ import annotations

import json
import re
import time
from contextvars import ContextVar
from typing import Any

from openai import OpenAI

_USAGE: ContextVar[list[dict[str, Any]] | None] = ContextVar("usage_log", default=None)


def parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("model JSON was not an object")
    return parsed


def collect_usage() -> tuple[list[dict[str, Any]], Any]:
    bucket: list[dict[str, Any]] = []
    return bucket, _USAGE.set(bucket)


def stop_usage(token: Any) -> None:
    _USAGE.reset(token)


def usage_row(
    *,
    stage: str,
    model: str,
    response: Any,
    wall_ms: float,
) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None) if usage is not None else None
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    return {
        "stage": stage,
        "model": model,
        "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage is not None else None,
        "completion_tokens": getattr(usage, "completion_tokens", None) if usage is not None else None,
        "cached_tokens": cached,
        "wall_ms": round(wall_ms, 1),
        "server_ms": getattr(response, "server_ms", None) if response is not None else None,
        "request_id": getattr(response, "id", None) if response is not None else None,
        "service_tier": getattr(response, "service_tier", None) if response is not None else None,
    }


def _record_usage(stage: str, model: str, response: Any, started: float) -> None:
    try:
        bucket = _USAGE.get()
        if bucket is None:
            return
        bucket.append(
            usage_row(
                stage=stage,
                model=model,
                response=response,
                wall_ms=(time.perf_counter() - started) * 1000,
            )
        )
    except Exception:
        return


class JsonChat:
    """Ask the model for one JSON object. Failures return None; callers decide the fallback."""

    def __init__(self, client: OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    def complete(
        self,
        content: str,
        *,
        temperature: float | None = None,
        stage: str = "llm",
    ) -> dict[str, Any] | None:
        started = time.perf_counter()
        response = None
        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": content}],
            }
            if temperature is not None:
                kwargs["temperature"] = temperature
            response = self._client.chat.completions.create(**kwargs)
            raw = response.choices[0].message.content or "{}"
            return parse_json_object(raw)
        except Exception:
            return None
        finally:
            _record_usage(stage, self._model, response, started)
