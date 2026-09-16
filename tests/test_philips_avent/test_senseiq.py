"""Unit tests for the SenseIQ data-point decoders.

Import-clean like the other module tests: conftest.py puts the philips_avent
directory on sys.path, so ``senseiq`` imports without pulling in Home Assistant.

The fixtures are the exact DPS 3 / DPS 4 values captured from an SCD9xx (SenseIQ
product 7d9t0rygsm7ztnww).
"""
import senseiq

# DPS 4 as it comes over the wire: base64 of an ASCII-hex string of the JSON.
DP4_RAW = (
    "N2IyMjczNzQyMjNhMzEzNzM4MzkzNDMwMzkzNzM5MzQyYzIyNzM2NDIyM2EzMTMwMzAzMzM4"
    "MmMyMjYzNzM3MzIyM2EyMjZjMjIyYzIyNjM3MzczNjQyMjNhMzYzMDJjMjI3MzczNjQyMjNh"
    "NWI3YjIyNjEyMjNhMzIzMTM2N2QyYzdiMjI2YzIyM2EzODM2N2QyYzdiMjI2MTIyM2EzOTM0"
    "MzY3ZDJjN2IyMjZjMjIzYTMzMzUzMDdkMmM3YjIyNjQyMjNhMzEzNzMxMzY3ZDJjN2IyMjZj"
    "MjIzYTM5MzI3ZDJjN2IyMjY0MjIzYTMzMzIzNzM0N2QyYzdiMjI2YzIyM2EzNDMwMzY3ZDJj"
    "N2IyMjY0MjIzYTMzMzQ3ZDJjN2IyMjZjMjIzYTMyMzczMTdkMmM3YjIyNjQyMjNhMzEzMDM3"
    "Mzc3ZDJjN2IyMjZjMjIzYTMxMzMzNzM3N2QyYzdiMjI2NDIyM2EzMTMzMzM3ZDVkN2Q="
)


def test_status_no_baby():
    out = senseiq.decode_status('{"r":"o","br":0}')
    assert out["breaths_per_minute"] is None  # 0 -> not reported
    assert out["state"] == "o"


def test_status_breathing():
    out = senseiq.decode_status('{"r":"i","br":42}')
    assert out["breaths_per_minute"] == 42
    assert out["state"] == "i"


def test_status_rejects_non_senseiq():
    assert senseiq.decode_status('{"foo":1}') is None
    assert senseiq.decode_status("") is None
    assert senseiq.decode_status(None) is None


def test_sleep_session_real_capture():
    s = senseiq.decode_sleep_session(DP4_RAW)
    assert s is not None
    assert s["start"] == 1789409794
    assert s["duration_seconds"] == 10038
    assert s["current_stage"] == "light"
    assert s["current_stage_seconds"] == 60
    # Timeline decoded in order, stage codes mapped.
    assert s["stages"][0] == {"stage": "awake", "seconds": 216}
    assert s["stages"][1] == {"stage": "light", "seconds": 86}
    assert s["stages"][4] == {"stage": "deep", "seconds": 1716}
    # Arithmetic invariant observed on hardware: sum(ssd) + cssd == sd.
    total = sum(x["seconds"] for x in s["stages"])
    assert total + s["current_stage_seconds"] == s["duration_seconds"]
    # Per-stage totals.
    assert s["totals_seconds"]["deep"] == 1716 + 3274 + 34 + 1077 + 133
    assert s["totals_seconds"]["awake"] == 216 + 946


def test_sleep_session_rejects_non_session():
    assert senseiq.decode_sleep_session('{"br":0}') is None
    assert senseiq.decode_sleep_session("not base64!!") is None
    assert senseiq.decode_sleep_session(None) is None


def test_is_baby_present():
    # Breathing reported -> present.
    assert senseiq.is_baby_present('{"r":"m","br":38}') is True
    # A present-state flag with no breathing yet -> present.
    assert senseiq.is_baby_present('{"r":"b","br":0}') is True
    assert senseiq.is_baby_present('{"r":"a","br":0}') is True
    # "o" = empty crib -> absent.
    assert senseiq.is_baby_present('{"r":"o","br":0}') is False
    # No SenseIQ status at all -> unknown.
    assert senseiq.is_baby_present(None) is None
    assert senseiq.is_baby_present('{"foo":1}') is None


def test_sleep_session_rejects_negative_durations():
    # A malformed negative duration must not become a reading, and a negative
    # stage segment is dropped rather than skewing the totals.
    s = senseiq.decode_sleep_session(
        '{"st":1789409794,"sd":-5,"css":"l","cssd":-1,'
        '"ssd":[{"l":100},{"d":-20},{"a":50}]}'
    )
    assert s is not None
    assert s["duration_seconds"] is None
    assert s["current_stage_seconds"] is None
    assert s["stages"] == [{"stage": "light", "seconds": 100}, {"stage": "awake", "seconds": 50}]
    assert s["totals_seconds"] == {"awake": 50, "light": 100, "deep": 0}


def test_session_end_and_active():
    s = senseiq.decode_sleep_session(DP4_RAW)
    end = senseiq.session_end_timestamp(s)
    assert end == 1789409794 + 10038
    # Fresh (end ~ now) reads active; a day-old session does not.
    assert senseiq.is_session_active(s, now=end + 10) is True
    assert senseiq.is_session_active(s, now=end + 86400) is False
    # A session far in the future is rejected as clock-skewed/malformed.
    assert senseiq.is_session_active(s, now=end - 86400) is False
    assert senseiq.is_session_active(None) is False
