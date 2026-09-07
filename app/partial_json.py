"""Tolerant parsing of a JSON document that is still being streamed.

Given the prefix of a JSON object as it arrives token by token, `parse_partial`
returns the best-effort Python object for what has arrived so far, plus the
name of the top-level key that is still being written (or None if the object
is complete).

Strategy: walk the text once tracking string/escape state and an open-bracket
stack, then close everything that is still open and json.loads the result.
Trailing partial tokens (a half-written `tru`, a dangling comma, a key with no
value yet) are trimmed before closing.
"""

from __future__ import annotations

import json
import re

_TRAILING_JUNK = re.compile(r'(,\s*|:\s*|"[^"]*"\s*:\s*|,\s*"[^"]*"\s*)$')
_PARTIAL_LITERAL = re.compile(r'(?<=[\[,:\s])(t|tr|tru|f|fa|fal|fals|n|nu|nul|-?\d*\.?\d*[eE]?[-+]?)$')


def _close(text: str) -> str:
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in text:
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
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
    if in_str:
        # drop a dangling escape and close the string
        if esc:
            text = text[:-1]
        text += '"'
    # strip dangling separators / bare keys / half literals
    for _ in range(3):
        stripped = _TRAILING_JUNK.sub("", text)
        stripped = _PARTIAL_LITERAL.sub("", stripped)
        stripped = _TRAILING_JUNK.sub("", stripped)
        if stripped == text:
            break
        text = stripped
    return text + "".join(reversed(stack))


def parse_partial(text: str) -> tuple[dict, str | None]:
    """Return (object, streaming_key)."""
    start = text.find("{")
    if start < 0:
        return {}, None
    text = text[start:]
    obj: dict | None = None
    attempt = text
    for _ in range(4):
        fixed = _close(attempt)
        try:
            parsed = json.loads(fixed)
            if isinstance(parsed, dict):
                obj = parsed
                break
        except Exception:
            pass
        # Trim a dangling (possibly half-written) key string and retry.
        trimmed = re.sub(r',?\s*"[^"]*"?\s*$', "", attempt)
        trimmed = re.sub(r'[,:\s]+$', "", trimmed)
        if trimmed == attempt:
            break
        attempt = trimmed
    if obj is None:
        return {}, None
    # A complete document ends with the closing brace of the root object.
    complete = _root_complete(text)
    streaming_key = None if complete else _last_top_level_key(text)
    return obj, streaming_key


def _root_complete(text: str) -> bool:
    depth = 0
    in_str = False
    esc = False
    for ch in text:
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
        elif ch == "{" or ch == "[":
            depth += 1
        elif ch == "}" or ch == "]":
            depth -= 1
            if depth == 0:
                return True
    return False


def _last_top_level_key(text: str) -> str | None:
    """Name of the most recently opened top-level key."""
    depth = 0
    in_str = False
    esc = False
    buf = ""
    last_key = None
    pending_str = None
    for ch in text:
        if in_str:
            if esc:
                esc = False
                buf += ch
            elif ch == "\\":
                esc = True
                buf += ch
            elif ch == '"':
                in_str = False
                pending_str = buf
            else:
                buf += ch
            continue
        if ch == '"':
            in_str = True
            buf = ""
        elif ch == ":" and depth == 1 and pending_str is not None:
            last_key = pending_str
            pending_str = None
        elif ch in "{[":
            depth += 1
            pending_str = None
        elif ch in "}]":
            depth -= 1
            pending_str = None
        elif ch == ",":
            pending_str = None
    return last_key
