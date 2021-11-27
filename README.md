SkyPort terminal implemented as a console script written in Python
=====================================================================

# Description

Sky Port is an universal bus between user software and compute resources.
It can also be considered as a transportation layer between workload producers
and compute resource providers. Sky Port makes it easy to connect user software
to different cloud resources.

The current project represents a console program that uses swmclient python package
to utilize client API of the [core Sky Port daemon](https://github.com/skyworkflows/swm-core).

# How to run

## Requirements:

Python >= 3.9 is required.

* The following command setups all requirements in a local virtualenv directory:
```bash
make prepare-venv
```
* Load the python environment:
```bash
. .venv/bin/activate
```
* Run the script:
```bash
src/swm-console --help
```

# Development

Code autoformatting and validation tools start with the following commands:
```bash
make format
make check
```

# Contributing

We appreciate all contributions. If you are planning to contribute back bug-fixes, please do so
without any further discussion. If you plan to contribute new features, utility functions or extensions,
please first open an issue and discuss the feature with us.

# Lincese

We use a shared copyright model that enables all contributors to maintain the copyright on their contributions.

This software is licensed under the BSD-3-Clause license.
