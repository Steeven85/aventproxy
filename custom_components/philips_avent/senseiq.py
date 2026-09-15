"""Decode Philips Avent SenseIQ live status and sleep-session data."""
from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

STAGE_NAMES = {"a": "awake", "l": "light", "d": "deep"}
SLEEP_STAGES = ("awake", "light", "deep")

# If st + sd has not moved for this long, DPS 4 is considered to be the
# persisted last session rather than a live session.
SESSION_STALE_AFTER_SECONDS = 300

# Small tolerance for clock skew / server timestamps slightly ahead of HA.
SESSION_FUTURE_TOLERANCE_SECONDS = 120


def _looks_hex(text: str) -> bool:
    """True when the string is an even-length run of hex digits."""
    if not text or len(text) % 2:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in text)


def _decode_raw_json(raw: object) -> dict | None:
    """Decode a SenseIQ data point into a dict, or None if unreadable."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None

    text = raw.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None

    try:
        inner = decoded.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None

    if inner.startswith("{"):
        try:
            return json.loads(inner)
        except (json.JSONDecodeError, ValueError):
            return None

    if _looks_hex(inner):
        try:
            payload = bytes.fromhex(inner).decode("utf-8")
            return json.loads(payload)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    return None


def _to_int(value: object) -> int | None:
    """Coerce to int, rejecting bools and unparseable values."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _epoch(value: object) -> int | None:
    """Epoch seconds from a SenseIQ timestamp; tolerates milliseconds."""
    stamp = _to_int(value)
    if stamp is None or stamp <= 0:
        return None
    if stamp > 1_000_000_000_000:
        stamp //= 1000
    return stamp


def decode_status(raw: object) -> dict | None:
    """Decode DPS 3 (senseiq_status)."""
    data = _decode_raw_json(raw)
    if not isinstance(data, dict) or "br" not in data:
        return None

    bpm = _to_int(data.get("br"))
    return {
        "breaths_per_minute": bpm if (bpm is not None and bpm > 0) else None,
        "state": data.get("r"),
        "raw": data,
    }


def decode_sleep_session(raw: object) -> dict | None:
    """Decode DPS 4 (sleep_session_data)."""
    data = _decode_raw_json(raw)
    if not isinstance(data, dict) or "ssd" not in data:
        return None

    stages: list[dict] = []
    totals = {name: 0 for name in SLEEP_STAGES}

    for item in data.get("ssd") or []:
        if not isinstance(item, dict) or len(item) != 1:
            continue
        (code, seconds), = item.items()
        name = STAGE_NAMES.get(code)
        secs = _to_int(seconds)
        if name is None or secs is None:
            continue
        stages.append({"stage": name, "seconds": secs})
        totals[name] += secs

    return {
        "start": _epoch(data.get("st")),
        "duration_seconds": _to_int(data.get("sd")),
        "current_stage": STAGE_NAMES.get(data.get("css")),
        "current_stage_seconds": _to_int(data.get("cssd")),
        "stages": stages,
        "totals_seconds": totals,
    }


def session_end_timestamp(session: dict | None) -> int | None:
    """Return st + sd for a decoded session."""
    if not session:
        return None

    start = _to_int(session.get("start"))
    duration = _to_int(session.get("duration_seconds"))
    if start is None or duration is None or start <= 0 or duration < 0:
        return None

    return start + duration


def is_session_active(
    session: dict | None,
    *,
    now: float | int | datetime | None = None,
    stale_after_seconds: int = SESSION_STALE_AFTER_SECONDS,
) -> bool:
    """Return True when DPS 4 looks like a live, still-updating session.

    DPS 4 persists the last completed session. During a live session, st + sd
    stays close to the current time because sd grows as SenseIQ tracks stages.
    """
    end = session_end_timestamp(session)
    if end is None:
        return False

    if now is None:
        now_ts = time.time()
    elif isinstance(now, datetime):
        now_ts = now.timestamp()
    else:
        now_ts = float(now)

    age = now_ts - end

    # Too far in the future means malformed/clock-skewed data.
    if age < -SESSION_FUTURE_TOLERANCE_SECONDS:
        return False

    return age <= stale_after_seconds
