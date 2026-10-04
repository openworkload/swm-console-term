"""Unit tests for swmconsole.common helpers."""

from enum import Enum
from types import SimpleNamespace

from swmconsole.common import (
    is_flavor_only_node,
    is_job_active,
    job_state_code,
    main_node_ip,
    sort_jobs_for_overview,
    truncate_details,
)


class _State(Enum):
    R = "R"
    F = "F"


def test_job_state_code_from_enum() -> None:
    job = SimpleNamespace(state=_State.R)
    assert job_state_code(job) == "R"


def test_job_state_code_from_string() -> None:
    job = SimpleNamespace(state="Q")
    assert job_state_code(job) == "Q"


def test_is_job_active() -> None:
    assert is_job_active(SimpleNamespace(state="R")) is True
    assert is_job_active(SimpleNamespace(state="F")) is False
    assert is_job_active(SimpleNamespace(state="C")) is False


def test_sort_jobs_for_overview_active_first() -> None:
    jobs = [
        SimpleNamespace(state="F", submit_time="2026-01-02T00:00:00Z"),
        SimpleNamespace(state="R", submit_time="2026-01-01T00:00:00Z"),
        SimpleNamespace(state="Q", submit_time="2026-01-03T00:00:00Z"),
    ]
    sorted_jobs = sort_jobs_for_overview(jobs)
    assert [j.state for j in sorted_jobs] == ["Q", "R", "F"]


def test_main_node_ip_prefers_additional_properties() -> None:
    job = SimpleNamespace(
        additional_properties={"main_ip": "1.2.3.4"},
        main_ip="9.9.9.9",
        node_ips=["8.8.8.8"],
    )
    assert main_node_ip(job) == "1.2.3.4"


def test_main_node_ip_falls_back_to_node_ips() -> None:
    job = SimpleNamespace(additional_properties={}, main_ip=None, node_ips=["10.0.0.1"])
    assert main_node_ip(job) == "10.0.0.1"


def test_truncate_details() -> None:
    assert truncate_details("short") == "short"
    assert truncate_details("x" * 60, max_len=10) == "xxxxxxx..."


def test_is_flavor_only_node() -> None:
    flavor = SimpleNamespace(name="flavor")
    cpu = SimpleNamespace(name="cpu")
    assert is_flavor_only_node(SimpleNamespace(name="Standard_D2", resources=[flavor])) is True
    assert is_flavor_only_node(SimpleNamespace(name="swm-abc-main", resources=[flavor])) is False
    assert is_flavor_only_node(SimpleNamespace(name="node1", resources=[cpu])) is False
