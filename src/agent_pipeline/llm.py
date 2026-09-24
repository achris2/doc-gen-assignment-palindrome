"""One JSON completion client. Callers parse the dict into a schema they own."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI


def parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("model JSON was not an object")
    return parsed


class JsonChat:
    """Ask the model for one JSON object. Failures return None; callers decide the fallback."""

    def __init__(self, client: OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    def complete(self, content: str, *, temperature: float | None = None) -> dict[str, Any] | None:
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
