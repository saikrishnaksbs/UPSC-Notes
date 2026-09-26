"""Tolerant JSON extraction for model output."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def _balanced(text: str, open_ch: str, close_ch: str) -> str | None:
    start = text.find(open_ch)
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find(open_ch, start + 1)
    return None


def parse_json_loose(text: str) -> Any:
    """Parse JSON even when wrapped in prose or code fences. Raises ValueError on failure."""
    text = text.strip()
    candidates = [text]
    candidates += [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        chunk = _balanced(text, open_ch, close_ch)
        if chunk:
            candidates.append(chunk)
    for cand in candidates:
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            # Trailing commas are a common small-model mistake.
            try:
                return json.loads(re.sub(r",\s*([}\]])", r"\1", cand))
            except (json.JSONDecodeError, TypeError):
                continue
    raise ValueError(f"no JSON found in model output: {text[:200]!r}")
