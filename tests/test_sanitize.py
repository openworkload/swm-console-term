"""Unit tests for terminal escape sanitization."""

from types import SimpleNamespace

from swmconsole.common import sanitize_for_terminal, truncate_details
from swmconsole.run import job_to_dict, yaml_safe


def test_sanitize_strips_csi_colors() -> None:
    assert sanitize_for_terminal("\x1b[31mred\x1b[0m") == "red"


def test_sanitize_strips_osc_title() -> None:
    raw = "\x1b]0;evil title\x07safe"
    assert sanitize_for_terminal(raw) == "safe"


def test_sanitize_strips_c0_keeps_newline_tab() -> None:
    raw = "a\x07b\x08c\nd\te"
    assert sanitize_for_terminal(raw) == "abc\nd\te"


def test_sanitize_plain_uuid_unchanged() -> None:
    job_id = "10000000-0000-0000-0000-000000000000"
    assert sanitize_for_terminal(job_id) == job_id


def test_sanitize_utf8_unchanged() -> None:
    text = "job café — ok"
    # Em dash may be present in UTF-8 payloads; sanitize must not drop printable Unicode.
    assert sanitize_for_terminal(text) == text


def test_sanitize_incomplete_esc_tail() -> None:
    assert sanitize_for_terminal("ok\x1b") == "ok"
    assert sanitize_for_terminal("ok\x1b[") == "ok"


def test_sanitize_empty() -> None:
    assert sanitize_for_terminal("") == ""


def test_truncate_details_sanitizes() -> None:
    raw = "\x1b[31m" + ("x" * 60)
    out = truncate_details(raw, max_len=20)
    assert "\x1b" not in out
    assert out.endswith("...")
    assert len(out) == 20


def test_yaml_safe_strips_csi_in_strings() -> None:
    payload = {"details": "\x1b[31mbad\x1b[0m", "n": 1}
    assert yaml_safe(payload) == {"details": "bad", "n": 1}


def test_job_to_dict_strips_csi_in_details() -> None:
    job = SimpleNamespace(
        id="jid",
        submit_time="t0",
        start_time="t1",
        end_time="t2",
        state="R",
        state_details="\x1b]0;x\x07running",
        node_ips=["10.0.0.1"],
    )
    data = job_to_dict(job)
    assert data["details"] == "running"
    assert "\x1b" not in data["details"]
