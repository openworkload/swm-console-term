PYTHON=python3.12
VENV_BIN=.venv/bin

.PHONY: prepare-venv
.ONESHELL:
prepare-venv: .SHELLFLAGS := -euo pipefail -c
prepare-venv: SHELL := bash
prepare-venv:
	# Recreate cleanly: a stale .venv can leave python -> python3.12 with a missing target.
	$(PYTHON) -m venv --clear .venv
	test -x $(VENV_BIN)/python
	$(VENV_BIN)/python -m pip install --upgrade pip
	$(VENV_BIN)/python -m pip install --ignore-installed --no-deps -r requirements.txt
	$(VENV_BIN)/python -m pip install -e .
	# PyPI swmclient may lag local (e.g. purge_jobs); prefer sibling checkout when present.
	if [ -d ../swm-python-client ]; then
		$(VENV_BIN)/python -m pip install -e ../swm-python-client
	fi

.PHONY: format
format:
	. .venv/bin/activate
	$(VENV_BIN)/autoflake -i -r --ignore-init-module-imports swmconsole
	$(VENV_BIN)/black swmconsole
	$(VENV_BIN)/isort swmconsole

.PHONY: check
check:
	. .venv/bin/activate
	$(VENV_BIN)/flake8 swmconsole
	$(VENV_BIN)/mypy swmconsole

.PHONY: update-client-package
update-client-package:
	. .venv/bin/activate
	$(VENV_BIN)/python -m pip install --upgrade -e ../swm-python-client

.PHONY: requirements
requirements: requirements.txt
	make prepare-venv || true

requirements.txt: requirements.in
	@pip-compile $<

.PHONY: package
package:
	. .venv/bin/activate
	$(PYTHON) -m build

.PHONY: upload
upload:
	. .venv/bin/activate
	$(PYTHON) -m twine upload --verbose --config-file .pypirc dist/*

.PHONY: clean
clean:
	rm -fr ./dist
	rm -fr swmconsole.egg-info
	rm -fr build
	rm -fr .venv
