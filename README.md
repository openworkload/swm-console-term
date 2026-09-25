Sky Port terminal implemented as a console script written in Python
=====================================================================

# Description

Sky Port is an universal bus between user software and compute resources.
It can also be considered as a transportation layer between workload producers
and compute resource providers. Sky Port makes it easy to connect user software
to different cloud resources. This is a part of [Open Workload](http://openworkload.org) project.

The current project represents a console program that uses swmclient python package
to utilize client API of the [Sky Port core daemon](https://github.com/openworkload/swm-core).
The source code of this package can be found [here](https://github.com/openworkload/swm-console-term).

# How to run

## Requirements:

* Python >= 3.12 is required.
* The following command setups all requirements in a local virtualenv directory:
```bash
make prepare-venv
```

## Install from pypi:
```bash
pip install swmconsole
```

## Run:
```bash
swmconsole --help
```

# Main commands

Connection uses mutual TLS by default:
* URL: `https://<fqdn>:8443`
* Key / cert: `~/.swm/key.pem`, `~/.swm/cert.pem`
* CA: `/opt/swm/spool/secure/cluster/ca-chain-cert.pem`

## Jobs

```bash
# List all jobs for the authenticated user
swmconsole --job-list

# Show details for one job
swmconsole --job-show <job-id>

# Submit a job script
swmconsole --job-submit /path/to/script.job

# Cancel a running or queued job
swmconsole --job-cancel <job-id>

# Requeue a job
swmconsole --job-requeue <job-id>

# Permanently purge all jobs and related allocations (asks for confirmation)
swmconsole --job-purge
```

## Resources

```bash
# List remote sites / partitions
swmconsole --remote-list

# List nodes
swmconsole --node-list

# List available flavors
swmconsole --flavor-list

# List available images
swmconsole --image-list
```

## Options

```bash
# Omit table headers
swmconsole --job-list --no-header

# Print connection debug info
swmconsole --job-list --debug

# Print any command output as YAML (includes version: 1)
swmconsole --job-list --yaml
swmconsole --job-show <job-id> --yaml
```

# Development

## Tools

Code autoformatting and validation tools start with the following commands:
```bash
make format
make check
```

## Update swmclient

The current project uses swmclient python package heavily. That package is updated frequently,
thus to update swmclient from local machine one can use pip to update from wheel package:
```bash
pip install /path/to/swmclient/wheel/package
```

Or install from a local checkout in editable mode:
```bash
pip install -e /path/to/swm-python-client
```

# Contributing

We appreciate all contributions. If you are planning to contribute back bug-fixes, please do so
without any further discussion. If you plan to contribute new features, utility functions or extensions,
please first open an issue and discuss the feature with us.

# License

We use a shared copyright model that enables all contributors to maintain the copyright on their contributions.

This software is licensed under the BSD-3-Clause license.
