#!/usr/bin/env python3

import argparse
import io
import platform
import sys
import typing

from swmclient.api import SwmApi  # type: ignore
from swmclient.generated.models.resource import Resource  # type: ignore
from swmclient.generated.types import File  # type: ignore
from tabulate import tabulate


def main() -> None:
    parser = argparse.ArgumentParser(description="Sky Port terminal implemented as a console script.")
    group = parser.add_mutually_exclusive_group(required=True)

    parser.add_argument("--no-header", help="Do not print header in tables", action="store_true")

    group.add_argument("--job-info", help="Show single job details")
    group.add_argument("--job-submit", help="Submit a new job script")
    group.add_argument("--job-cancel", help="Cancel job")
    group.add_argument("--job-requeue", help="Requeue job")
    group.add_argument("--job-list", help="Show all jobs", action="store_true")
    group.add_argument("--remote-list", help="Show remote sites", action="store_true")
    group.add_argument("--node-list", help="Show nodes", action="store_true")
    group.add_argument("--flavor-list", help="Show available flavors", action="store_true")

    args = parser.parse_args()

    swm_api = SwmApi(
        url=f"https://{platform.node()}:8443",
        key_file="~/.swm/key.pem",
        cert_file="~/.swm/cert.pem",
        ca_file="/opt/swm/spool/secure/cluster/ca-chain-cert.pem",
    )

    if args.job_info:
        print_job_info(args, swm_api)
    elif args.job_submit:
        submit_new_job(args, swm_api)
    elif args.job_cancel:
        cancel_job(args, swm_api)
    elif args.job_requeue:
        requeue_job(args, swm_api)
    elif args.job_list:
        print_jobs(args, swm_api)
    elif args.remote_list:
        print_remote_sites(args, swm_api)
    elif args.node_list:
        print_nodes(args, swm_api)
    elif args.flavor_list:
        print_flavors(args, swm_api)


def print_job_info(args: argparse.Namespace, swm_api: SwmApi) -> None:
    job_id = args.job_info
    if (job := swm_api.get_job(job_id)) is not None:
        table = [
            ["ID", job.id],
            ["Name", job.name],
            ["State", job.state],
            ["Submit", job.submit_time],
            ["Start", job.start_time],
            ["End", job.end_time],
            ["Nodes", ", ".join(job.node_names)],
        ]
        print(tabulate(table, tablefmt="presto"))
    else:
        print("No job found")


def requeue_job(args: argparse.Namespace, swm_api: SwmApi) -> None:
    job_id = args.job_requeue
    if (output := swm_api.requeue_job(job_id)) is not None:
        for line in output.decode("utf-8").split("\n"):
            print(line.strip())
    else:
        print("No result")


def cancel_job(args: argparse.Namespace, swm_api: SwmApi) -> None:
    job_id = args.job_cancel
    if (output := swm_api.cancel_job(job_id)) is not None:
        for line in output.decode("utf-8").split("\n"):
            print(line.strip())
    else:
        print("No result")


def submit_new_job(args: argparse.Namespace, swm_api: SwmApi) -> None:
    path = args.job_submit
    with open(path, "rb", buffering=0) as f:
        io_bytes = io.BytesIO(f.read())
        io_obj: File = swm_api.submit_job(io_bytes)
        while True:
            if line := io_obj.payload.readline():
                print(line.decode("utf-8").strip())
            else:
                break


def find_resource(name: str, resources: typing.List[Resource]) -> typing.Optional[Resource]:
    for resource in resources:
        if resource.name == name:
            return resource
    return None


def get_res_storage(resources: typing.List[Resource]) -> str:
    if (res := find_resource("storage", resources)) is not None:
        if res.count >= 1000 * 1000:
            return str(int(res.count / (1000 * 1000 * 1000))) + " GB"
        return str(res.count) + " B"
    return ""


def get_res_mem(resources: typing.List[Resource]) -> str:
    if (res := find_resource("mem", resources)) is not None:
        if res.count >= 1000 * 1000:
            return str(int(res.count / (1000 * 1000))) + " MB"
        return str(res.count) + " B"
    return ""


def get_res_cpus(resources: typing.List[Resource]) -> str:
    if (res := find_resource("cpus", resources)) is not None:
        return str(res.count)
    return ""


def print_nodes(args: argparse.Namespace, swm_api: SwmApi) -> None:
    nodes = swm_api.get_nodes()
    if isinstance(nodes, list):
        headers = [] if args.no_header else ["ID", "Name", "Power", "Alloc", "Storage", "Mem", "CPUs"]
        table = []
        for node in nodes:
            table.append(
                [
                    node.id,
                    node.name,
                    node.state_power,
                    node.state_alloc,
                    get_res_storage(node.resources),
                    get_res_mem(node.resources),
                    get_res_cpus(node.resources),
                ]
            )
        print(tabulate(table, headers=headers, tablefmt="presto"))
    else:
        print(f"Wrong output: {nodes}", file=sys.stderr)
        sys.exit(1)


def print_remote_sites(args: argparse.Namespace, swm_api: SwmApi) -> None:
    remotes = swm_api.get_remote_sites()
    if isinstance(remotes, list):
        headers = [] if args.no_header else ["ID", "Name", "Kind", "Default image ID", "Default flavor ID"]
        table = []
        for remote in remotes:
            table.append(
                [
                    remote.id,
                    remote.name,
                    remote.kind,
                    remote.default_image_id,
                    remote.default_flavor_id,
                ]
            )
        print(tabulate(table, headers=headers, tablefmt="presto"))
    else:
        print(f"Wrong output: {remotes}", file=sys.stderr)
        sys.exit(1)


def print_jobs(args: argparse.Namespace, swm_api: SwmApi) -> None:
    jobs = swm_api.get_jobs()
    if isinstance(jobs, list):
        headers = [] if args.no_header else ["ID", "Name", "State", "Submit time", "Start time", "End time", "Nodes"]
        table = []
        for job in jobs:
            table.append(
                [
                    job.id,
                    job.name,
                    job.state,
                    job.submit_time,
                    job.start_time,
                    job.end_time,
                    ", ".join(job.node_names),
                ]
            )
        print(tabulate(table, headers=headers, tablefmt="presto"))
    else:
        print(f"Wrong output: {jobs}", file=sys.stderr)
        sys.exit(1)


def print_flavors(args: argparse.Namespace, swm_api: SwmApi) -> None:
    flavors = swm_api.get_flavors()
    if isinstance(flavors, list):
        headers = [] if args.no_header else ["ID", "Name", "Storage", "Mem", "CPUs", "Price"]
        table = []
        for flavor in flavors:
            table.append(
                [
                    flavor.id,
                    flavor.name,
                    get_res_storage(flavor.resources),
                    get_res_mem(flavor.resources),
                    get_res_cpus(flavor.resources),
                    flavor.price,
                ]
            )
        print(tabulate(table, headers=headers, tablefmt="presto"))
    else:
        print(f"Wrong output: {flavors}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
