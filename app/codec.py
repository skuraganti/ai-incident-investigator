"""Shared within this project only: bounded canonical evidence JSON."""

import hashlib
import json
import math


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def bounded(value, depth=0):
    if depth > 12:
        raise ValueError("JSON nesting exceeds bound")
    if value is None or type(value) is bool:
        return
    if isinstance(value, str):
        if len(value) > 30_000:
            raise ValueError("Text exceeds bound")
        value.encode("utf-8")
    elif type(value) in {int, float}:
        if abs(value) > 1e15 or not math.isfinite(value):
            raise ValueError("Numeric value exceeds bound")
    elif isinstance(value, list) and len(value) <= 200:
        for item in value:
            bounded(item, depth + 1)
    elif isinstance(value, dict) and len(value) <= 100:
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Object keys must be text")
            bounded(item, depth + 1)
    else:
        raise ValueError("Unsupported bounded JSON")


def parse_json(text, cap=32768):
    if not isinstance(text, str) or len(text.encode("utf-8")) > cap:
        raise ValueError("Output exceeds bound")
    stripped = text.strip()
    if stripped.startswith("```json\n") and stripped.endswith("\n```"):
        stripped = stripped[8:-4]

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    try:
        result = json.loads(
            stripped,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        bounded(result)
        return result
    except (RecursionError, OverflowError, UnicodeError) as exc:
        raise ValueError("Invalid bounded JSON") from exc
