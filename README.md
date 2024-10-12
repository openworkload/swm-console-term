Sky Port terminal implemented as a console script written in Python
=====================================================================

# Description

Sky Port is an universal bus between user software and compute resources.
It can also be considered as a transportation layer between workload producers
and compute resource providers. Sky Port makes it easy to connect user software
to different cloud resources. This is a parto of [Open Workload](http://openworkload.org) project.

The current project represents a console program that uses swmclient python package
to utilize client API of the [Sky Port core daemon](https://github.com/openworkload/swm-core).

# How to run

## Requirements:

* Python >= 3.10 is required.
* The following command setups all requirements in a local virtualenv directory:
```bash
make prepare-venv
```

## Run the script:
```bash
swm-console --help
```

# Development

## Tools

Code autoformatting and validation tools start with the following commands:
```bash
make format
make check
```

## Update swmclient

The current project uses swmclient python package heavely. That package is updated frequently,
thus to update swmclient from local machine one can use pip to update from wheel package:
```bash
pip install /path/to/swmclient/wheel/package
```

# Contributing

We appreciate all contributions. If you are planning to contribute back bug-fixes, please do so
without any further discussion. If you plan to contribute new features, utility functions or extensions,
please first open an issue and discuss the feature with us.

# License

We use a shared copyright model that enables all contributors to maintain the copyright on their contributions.

This software is licensed under the BSD-3-Clause license.
