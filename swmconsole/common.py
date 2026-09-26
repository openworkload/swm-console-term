"""Shared helpers for swmconsole commands and the overview TUI."""

from __future__ import annotations

import typing
from enum import Enum

# Meta-filter token for jobs that are still in progress (not F/C/E).
JOB_STATE_ACTIVE = "active"

# CLI aliases (case-insensitive) -> API job state letter codes.
JOB_STATE_ALIASES: typing.Dict[str, str] = {
    "a": JOB_STATE_ACTIVE,
    "active": JOB_STATE_ACTIVE,
    "r": "R",
    "running": "R",
    "q": "Q",
    "queued": "Q",
    "w": "W",
    "waiting": "W",
    "f": "F",
    "finished": "F",
    "e": "E",
    "error": "E",
    "t": "T",
    "transferring": "T",
    "c": "C",
    "canceled": "C",
    "cancelled": "C",
}

# Terminal states treated as non-active (meta-filter "active" / overview grouping).
JOB_STATE_INACTIVE: typing.FrozenSet[str] = frozenset({"F", "C", "E"})


def job_state_code(job: typing.Any) -> str:
    state = getattr(job, "state", None)
    if isinstance(state, Enum):
        return str(state.value)
    return str(state or "")


def is_job_active(job: typing.Any) -> bool:
    return job_state_code(job) not in JOB_STATE_INACTIVE


def sort_jobs_for_overview(jobs: list[typing.Any]) -> list[typing.Any]:
    """Active jobs first (newest submit first), then inactive (newest submit first)."""

    def submit_time(job: typing.Any) -> str:
        return str(getattr(job, "submit_time", None) or "")

    active = [j for j in jobs if is_job_active(j)]
    inactive = [j for j in jobs if not is_job_active(j)]
    active.sort(key=submit_time, reverse=True)
    inactive.sort(key=submit_time, reverse=True)
    return active + inactive


def main_node_ip(job: typing.Any) -> str:
    """Return the job main node public IP (API main_ip, else first node_ips entry)."""
    additional = getattr(job, "additional_properties", None) or {}
    if isinstance(additional, dict):
        main_ip = additional.get("main_ip")
        if main_ip:
            return str(main_ip)
    main_ip = getattr(job, "main_ip", None)
    if main_ip:
        return str(main_ip)
    ips = getattr(job, "node_ips", None) or []
    if not ips:
        return ""
    return str(ips[0])


def truncate_details(details: typing.Optional[str], max_len: int = 50) -> str:
    text = details or ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def is_flavor_only_node(node: typing.Any) -> bool:
    if node.name.startswith("swm-"):
        return False
    for res in node.resources:
        if res.name == "flavor":
            return True
    return False


def is_created_cloud_node(node: typing.Any) -> bool:
    """True for nodes created for cloud jobs (names like swm-<jobid>-...)."""
    name = getattr(node, "name", "") or ""
    return name.startswith("swm-") and not is_flavor_only_node(node)


def is_cloud_partition_manager(node: typing.Any) -> bool:
    """True for the main node of a created cloud partition (one per cloud job partition)."""
    if not is_created_cloud_node(node):
        return False
    name = getattr(node, "name", "") or ""
    if name.endswith("-main"):
        return True
    for role in getattr(node, "roles", None) or []:
        role_name = getattr(role, "name", None)
        if isinstance(role_name, Enum):
            role_name = role_name.value
        if str(role_name or "") == "partition":
            return True
    return False
