#!/usr/bin/env python3

import argparse
import io
import socket
import sys
import typing
from enum import Enum

import httpx
import yaml
from swmclient.api import SwmApi  # type: ignore[import-not-found]
from swmclient.generated.models.resource import Resource  # type: ignore[import-not-found]
from swmclient.generated.types import File  # type: ignore[import-not-found]
from tabulate import tabulate

from .common import (
    JOB_STATE_ACTIVE,
    JOB_STATE_ALIASES,
    JOB_STATE_INACTIVE,
    is_flavor_only_node,
    job_state_code,
    main_node_ip,
    truncate_details,
)

URL = f"https://{socket.getfqdn()}:8443"
KEY_FILE = "~/.swm/key.pem"
CERT_FILE = "~/.swm/cert.pem"
CA_FILE = "/opt/swm/spool/secure/cluster/ca-chain-cert.pem"
YAML_VERSION = 1


def parse_job_state(value: str) -> str:
    """Accept a one-letter code, full state name, or meta-state; return filter token."""
    key = value.strip().lower()
    if key in JOB_STATE_ALIASES:
        return JOB_STATE_ALIASES[key]
    allowed = ", ".join(sorted(set(JOB_STATE_ALIASES)))
    raise argparse.ArgumentTypeError(f"invalid job state {value!r}; expected one of: {allowed}")


def job_matches_state_filter(job: typing.Any, state_filter: str) -> bool:
    code = job_state_code(job)
    if state_filter == JOB_STATE_ACTIVE:
        return code not in JOB_STATE_INACTIVE
    return code == state_filter


def parse_interval(value: str) -> float:
    try:
        interval = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid interval {value!r}; expected a number") from exc
    if interval <= 0:
        raise argparse.ArgumentTypeError("interval must be > 0")
    return interval


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Sky Port terminal. With no command options, opens an interactive overview TUI "
            "(htop-style). Use --interval to change the refresh period."
        )
    )
    group = parser.add_mutually_exclusive_group(required=False)

    parser.add_argument("--no-header", help="Do not print header in tables", action="store_true")
    parser.add_argument("--debug", help="Enable debug messages", action="store_true")
    parser.add_argument("--yaml", help="Print output in YAML format", action="store_true")
    parser.add_argument(
        "--state",
        metavar="STATE",
        type=parse_job_state,
        help=(
            "Filter --job-list by job state: letter or name (R/running, Q/queued, "
            "W/waiting, F/finished, E/error, T/transferring, C/canceled), "
            "or meta-state A/active (all except finished, canceled, and error)"
        ),
    )
    parser.add_argument(
        "--interval",
        metavar="SECONDS",
        type=parse_interval,
        default=None,
        help="Overview refresh interval in seconds (default: 5; only for the default overview TUI)",
    )

    group.add_argument("--job-show", help="Show single job details")
    group.add_argument("--job-submit", help="Submit a new job script")
    group.add_argument("--job-cancel", help="Cancel job")
    group.add_argument("--job-requeue", help="Requeue job")
    group.add_argument("--job-list", help="Show all jobs", action="store_true")
    group.add_argument(
        "--job-purge",
        help="Permanently purge all jobs for the authenticated user",
        action="store_true",
    )
    group.add_argument("--remote-list", help="Show remote sites", action="store_true")
    group.add_argument("--node-list", help="Show nodes", action="store_true")
    group.add_argument("--flavor-list", help="Show available flavors", action="store_true")
    group.add_argument("--image-list", help="Show available images", action="store_true")

    args = parser.parse_args()

    command_selected = any(
        [
            args.job_show,
            args.job_submit,
            args.job_cancel,
            args.job_requeue,
            args.job_list,
            args.job_purge,
            args.remote_list,
            args.node_list,
            args.flavor_list,
            args.image_list,
        ]
    )

    if args.state is not None and not args.job_list:
        parser.error("--state can only be used with --job-list")
    if args.interval is not None and command_selected:
        parser.error("--interval can only be used with the default overview (no other command)")
    if not command_selected and args.yaml:
        parser.error("--yaml cannot be used with the overview TUI")

    if args.debug:
        print(f"[DEBUG] url: {URL}", file=sys.stderr)
        print(f"[DEBUG] key: {KEY_FILE}", file=sys.stderr)
        print(f"[DEBUG] cert: {CERT_FILE}", file=sys.stderr)
        print(f"[DEBUG] ca: {CA_FILE}", file=sys.stderr)
    swm_api = SwmApi(url=URL, key_file=KEY_FILE, cert_file=CERT_FILE, ca_file=CA_FILE)

    if args.job_show:
        print_job_show(args, swm_api)
    elif args.job_submit:
        submit_new_job(args, swm_api)
    elif args.job_cancel:
        cancel_job(args, swm_api)
    elif args.job_requeue:
        requeue_job(args, swm_api)
    elif args.job_list:
        print_jobs(args, swm_api)
    elif args.job_purge:
        purge_jobs(args, swm_api)
    elif args.remote_list:
        print_remote_sites(args, swm_api)
    elif args.node_list:
        print_nodes(args, swm_api)
    elif args.flavor_list:
        print_flavors(args, swm_api)
    elif args.image_list:
        print_images(args, swm_api)
    else:
        from .overview import DEFAULT_INTERVAL, run_overview

        interval = args.interval if args.interval is not None else DEFAULT_INTERVAL
        run_overview(swm_api, interval=interval)


def yaml_safe(value: typing.Any) -> typing.Any:
    """Convert API/model values into types that yaml.safe_dump can represent."""
    if isinstance(value, Enum):
        return yaml_safe(value.value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): yaml_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [yaml_safe(item) for item in value]
    return str(value)


def print_as_yaml(data: typing.Mapping[str, typing.Any]) -> None:
    payload: typing.Dict[str, typing.Any] = {"version": YAML_VERSION}
    payload.update(yaml_safe(dict(data)))
    yaml.safe_dump(payload, sys.stdout, sort_keys=False, allow_unicode=True)


def print_action_result(args: argparse.Namespace, output: typing.Optional[bytes], empty_msg: str = "No result") -> None:
    if output is None:
        if args.yaml:
            print_as_yaml({"message": empty_msg})
        else:
            print(empty_msg)
        return
    lines = [line.strip() for line in output.decode("utf-8").split("\n") if line.strip()]
    if args.yaml:
        print_as_yaml({"output": "\n".join(lines)})
    else:
        for line in lines:
            print(line)


def job_to_dict(job: typing.Any, *, truncate: bool = False, main_ip_only: bool = False) -> typing.Dict[str, typing.Any]:
    details = job.state_details or ""
    if truncate:
        details = truncate_details(details)
    data: typing.Dict[str, typing.Any] = {
        "id": job.id,
        "submit_time": job.submit_time,
        "start_time": job.start_time,
        "end_time": job.end_time,
        "state": job.state,
        "details": details,
    }
    if main_ip_only:
        data["main_ip"] = main_node_ip(job)
    else:
        data["node_ips"] = list(job.node_ips)
    return data


def print_job_show(args: argparse.Namespace, swm_api: SwmApi) -> None:
    job_id = args.job_show
    if (job := swm_api.get_job(job_id)) is not None:
        if args.yaml:
            print_as_yaml({"job": job_to_dict(job)})
            return
        details = job.state_details or ""
        table = [
            ["ID", job.id],
            ["Submit", job.submit_time],
            ["Start", job.start_time],
            ["End", job.end_time],
            ["Node IPs", ", ".join(job.node_ips)],
            ["State", job.state],
        ]
        # Keep Details out of the table so multiline text is not easy to miss
        # among tabulate continuation rows (label column blank on lines 2+).
        print(tabulate(table, tablefmt="presto"))
        if "\n" in details:
            print(" Details  |")
            for line in details.splitlines():
                print(f"          | {line}")
        else:
            print(f" Details  | {details}")
    else:
        if args.yaml:
            print_as_yaml({"job": None, "message": "No job found"})
        else:
            print("No job found")


def requeue_job(args: argparse.Namespace, swm_api: SwmApi) -> None:
    print_action_result(args, swm_api.requeue_job(args.job_requeue))


def cancel_job(args: argparse.Namespace, swm_api: SwmApi) -> None:
    print_action_result(args, swm_api.cancel_job(args.job_cancel))


def purge_jobs(args: argparse.Namespace, swm_api: SwmApi) -> None:
    jobs = swm_api.get_jobs()
    job_count = len(jobs) if isinstance(jobs, list) else 0
    print(f"This will permanently purge {job_count} job(s) and related allocations from Sky Port.", file=sys.stderr)
    answer = input("Do you really want to purge all your jobs? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        if args.yaml:
            print_as_yaml({"aborted": True, "message": "Aborted."})
        else:
            print("Aborted.")
        return
    print_action_result(args, swm_api.purge_jobs())


def submit_new_job(args: argparse.Namespace, swm_api: SwmApi) -> None:
    path = args.job_submit
    try:
        with open(path, "rb", buffering=0) as f:
            io_bytes = io.BytesIO(f.read())
    except OSError as exc:
        msg = f"Cannot read job script '{path}': {exc.strerror}"
        if args.yaml:
            print_as_yaml({"error": msg})
        else:
            print(msg, file=sys.stderr)
        sys.exit(1)
    try:
        io_obj: File = swm_api.submit_job(io_bytes)
    except httpx.TimeoutException as exc:
        # Job may already be queued; SkyPort accepted the write after the client gave up.
        msg = (
            f"Job submit timed out waiting for Sky Port ({exc}). "
            "The job may still have been accepted; check with --job-list."
        )
        if args.yaml:
            print_as_yaml({"error": msg, "hint": "job-list"})
        else:
            print(msg, file=sys.stderr)
        sys.exit(1)
    if io_obj is None:
        msg = "Job submit returned no response from Sky Port."
        if args.yaml:
            print_as_yaml({"error": msg})
        else:
            print(msg, file=sys.stderr)
        sys.exit(1)
    lines: typing.List[str] = []
    while True:
        if raw := io_obj.payload.readline():
            lines.append(raw.decode("utf-8").strip())
        else:
            break
    if args.yaml:
        print_as_yaml({"output": "\n".join(out for out in lines if out)})
    else:
        for out in lines:
            print(out)


def find_resource(name: str, resources: typing.List[Resource]) -> typing.Optional[Resource]:
    for resource in resources:
        if resource.name == name:
            return resource
    return None


def get_res_storage(resources: typing.List[Resource]) -> str:
    if (res := find_resource("storage", resources)) is not None:
        count = res.count if isinstance(res.count, int) else 0
        if count >= 1000 * 1000:
            return str(int(count / (1000 * 1000 * 1000))) + " GB"
        return str(count) + " B"
    return ""


def get_res_mem(resources: typing.List[Resource]) -> str:
    if (res := find_resource("mem", resources)) is not None:
        count = res.count if isinstance(res.count, int) else 0
        if count >= 1000 * 1000:
            return str(int(count / (1000 * 1000))) + " MB"
        return str(count) + " B"
    return ""


def get_res_cpus(resources: typing.List[Resource]) -> str:
    if (res := find_resource("cpus", resources)) is not None:
        return str(res.count)
    return ""


def get_res_gpus(resources: typing.List[Resource]) -> str:
    if (res := find_resource("gpus", resources)) is not None:
        return str(res.count)
    return ""


def print_nodes(args: argparse.Namespace, swm_api: SwmApi) -> None:
    nodes = swm_api.get_nodes()
    if isinstance(nodes, list):
        rows = []
        for node in nodes:
            if is_flavor_only_node(node):
                continue
            rows.append(
                {
                    "id": node.id,
                    "name": node.name,
                    "power": node.state_power,
                    "alloc": node.state_alloc,
                    "storage": get_res_storage(node.resources),
                    "mem": get_res_mem(node.resources),
                    "cpus": get_res_cpus(node.resources),
                    "gpus": get_res_gpus(node.resources),
                }
            )
        if args.yaml:
            print_as_yaml({"nodes": rows})
            return
        headers = [] if args.no_header else ["ID", "Name", "Power", "Alloc", "Storage", "Mem", "CPUs", "GPUs"]
        table = [
            [row["id"], row["name"], row["power"], row["alloc"], row["storage"], row["mem"], row["cpus"], row["gpus"]]
            for row in rows
        ]
        print(tabulate(table, headers=headers, tablefmt="presto"))
    else:
        print(f"Wrong output: {nodes}", file=sys.stderr)
        sys.exit(1)


def print_remote_sites(args: argparse.Namespace, swm_api: SwmApi) -> None:
    remotes = swm_api.get_remote_sites()
    if isinstance(remotes, list):
        rows = [
            {
                "id": remote.id,
                "name": remote.name,
                "kind": remote.kind,
                "default_image_id": remote.default_image_id,
                "default_flavor_id": remote.default_flavor_id,
            }
            for remote in remotes
        ]
        if args.yaml:
            print_as_yaml({"remotes": rows})
            return
        headers = [] if args.no_header else ["ID", "Name", "Kind", "Default image ID", "Default flavor ID"]
        table = [
            [row["id"], row["name"], row["kind"], row["default_image_id"], row["default_flavor_id"]] for row in rows
        ]
        print(tabulate(table, headers=headers, tablefmt="presto"))
    else:
        print(f"Wrong output: {remotes}", file=sys.stderr)
        sys.exit(1)


def print_jobs(args: argparse.Namespace, swm_api: SwmApi) -> None:
    jobs = swm_api.get_jobs()
    if not isinstance(jobs, list):
        print(f"Wrong output: {jobs}", file=sys.stderr)
        sys.exit(1)
    if args.state is not None:
        jobs = [job for job in jobs if job_matches_state_filter(job, args.state)]
    if not jobs:
        msg = "No jobs found"
        if args.state is not None:
            msg = f"No jobs found with state filter {args.state!r}"
        if args.yaml:
            print_as_yaml({"jobs": [], "message": msg})
        else:
            print(msg)
        return
    if args.yaml:
        print_as_yaml({"jobs": [job_to_dict(job, main_ip_only=True) for job in jobs]})
        return
    headers = (
        []
        if args.no_header
        else [
            "ID",
            "Submit time",
            "Start time",
            "End time",
            "Main IP",
            "State",
            "Details",
        ]
    )
    table = []
    for job in jobs:
        table.append(
            [
                job.id,
                job.submit_time,
                job.start_time,
                job.end_time,
                main_node_ip(job),
                job.state,
                truncate_details(job.state_details),
            ]
        )
    print(tabulate(table, headers=headers, tablefmt="presto"))


def print_flavors(args: argparse.Namespace, swm_api: SwmApi) -> None:
    flavors = swm_api.get_flavors()
    if isinstance(flavors, list):
        rows = [
            {
                "id": flavor.id,
                "name": flavor.name,
                "storage": get_res_storage(flavor.resources),
                "mem": get_res_mem(flavor.resources),
                "cpus": get_res_cpus(flavor.resources),
                "price": flavor.price,
            }
            for flavor in flavors
        ]
        if args.yaml:
            print_as_yaml({"flavors": rows})
            return
        headers = [] if args.no_header else ["ID", "Name", "Storage", "Mem", "CPUs", "Price"]
        table = [[row["id"], row["name"], row["storage"], row["mem"], row["cpus"], row["price"]] for row in rows]
        print(tabulate(table, headers=headers, tablefmt="presto"))
    else:
        print(f"Wrong output: {flavors}", file=sys.stderr)
        sys.exit(1)


def print_images(args: argparse.Namespace, swm_api: SwmApi) -> None:
    images = swm_api.get_images()
    if isinstance(images, list):
        rows = [
            {
                "name": image.name,
                "kind": image.kind,
                "comment": image.comment,
            }
            for image in images
        ]
        if args.yaml:
            print_as_yaml({"images": rows})
            return
        headers = [] if args.no_header else ["Name", "Kind", "Comment"]
        table = [[row["name"], row["kind"], row["comment"]] for row in rows]
        print(tabulate(table, headers=headers, tablefmt="presto"))
    else:
        print(f"Wrong output: {images}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
