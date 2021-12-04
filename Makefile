PYTHON=python3
VENV_BIN=.venv/bin

.PHONY: prepare-venv
.ONESHELL:
prepare-venv: .SHELLFLAGS := -euo pipefail -c
prepare-venv: SHELL := bash
prepare-venv:
	$(PYTHON) -m pip install 'virtualenv>=16.4.3' 'pip-tools'
	virtualenv --system-site-packages .venv
	$(VENV_BIN)/pip install --ignore-installed --no-deps -r requirements.txt

.PHONY: format
format:
	$(VENV_BIN)/autoflake -i -r --ignore-init-module-imports src
	$(VENV_BIN)/black src
	$(VENV_BIN)/isort src

.PHONY: check
check:
	. .venv/bin/activate
	$(VENV_BIN)/flake8 src
	$(VENV_BIN)/mypy src

.PHONY: requirements
requirements: requirements.txt
	make prepare-venv || true

requirements.txt: requirements.in
	@pip-compile $<
