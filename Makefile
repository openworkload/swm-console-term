PYTHON=python3
VENV_BIN=.venv/bin

.PHONY: prepare-venv
.ONESHELL:
prepare-venv: .SHELLFLAGS := -euo pipefail -c
prepare-venv: SHELL := bash
prepare-venv:
	virtualenv --system-site-packages .venv
	$(VENV_BIN)/pip install --ignore-installed --no-deps -r requirements.txt

.PHONY: format
format:
	. .venv/bin/activate
	$(VENV_BIN)/autoflake -i -r --ignore-init-module-imports src
	$(VENV_BIN)/black src
	$(VENV_BIN)/isort src

.PHONY: check
check:
	. .venv/bin/activate
	$(VENV_BIN)/flake8 src
	$(VENV_BIN)/mypy src

.PHONY: update-client-package
update-client-package:
	. .venv/bin/activate
	pip install --upgrade -e ../swm-python-client

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
