"""Unit tests for CLI parsers in swmconsole.run."""

import argparse
from types import SimpleNamespace

import pytest

from swmconsole.run import job_matches_state_filter, parse_interval, parse_job_state


def test_parse_job_state_aliases() -> None:
    assert parse_job_state("R") == "R"
    assert parse_job_state("running") == "R"
    assert parse_job_state("A") == "active"
    assert parse_job_state("active") == "active"
    assert parse_job_state("cancelled") == "C"


def test_parse_job_state_invalid() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="invalid job state"):
        parse_job_state("nope")


def test_parse_interval() -> None:
    assert parse_interval("2.5") == 2.5
    with pytest.raises(argparse.ArgumentTypeError, match="expected a number"):
        parse_interval("x")
    with pytest.raises(argparse.ArgumentTypeError, match="must be > 0"):
        parse_interval("0")


def test_job_matches_state_filter() -> None:
    running = SimpleNamespace(state="R")
    finished = SimpleNamespace(state="F")
    assert job_matches_state_filter(running, "R") is True
    assert job_matches_state_filter(finished, "R") is False
    assert job_matches_state_filter(running, "active") is True
    assert job_matches_state_filter(finished, "active") is False


def test_cli_help_exits_zero() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "swmconsole.run", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Sky Port terminal" in result.stdout
