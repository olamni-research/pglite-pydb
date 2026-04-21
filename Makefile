# pglite-pydb — Linux/macOS contributor entrypoint.
#
# Canonical task logic lives in tasks.py (stdlib-only Python). This
# Makefile is a thin delegator so Linux/macOS contributors who prefer
# `make <target>` get identical behaviour to Windows contributors who
# run `uv run python tasks.py <target>`. Single source of truth; no
# duplication (FR-014, tasks T038-T041).

PYTHON := uv run python
TASKS  := $(PYTHON) tasks.py

.PHONY: help dev test examples lint quick install fmt clean status regen-sample-sha

help:
	@echo "pglite-pydb — available tasks"
	@echo "(all targets delegate to 'uv run python tasks.py <name>')"
	@echo ""
	@echo "  make dev         Run full development workflow (lint + examples + tests)"
	@echo "  make test        Run the test suite only"
	@echo "  make examples    Run example tests only"
	@echo "  make lint        Run linting only (pre-commit)"
	@echo "  make quick       Quick dev checks (install + lint + import smoke)"
	@echo "  make install     Install in development mode (uv sync)"
	@echo "  make fmt         Auto-fix formatting (ruff format)"
	@echo "  make clean       Remove build artefacts and caches"
	@echo "  make status      Print environment + install status"
	@echo ""
	@echo "Windows (PowerShell): use 'uv run python tasks.py <name>' instead."

dev:
	@$(TASKS) dev

test:
	@$(TASKS) test

examples:
	@$(TASKS) examples

lint:
	@$(TASKS) lint

quick:
	@$(TASKS) quick

install:
	@$(TASKS) install

fmt:
	@$(TASKS) fmt

clean:
	@$(TASKS) clean

status:
	@$(TASKS) status

# Recompute examples/windows_sample_db/data/sample_db.sql.sha256 from the
# current sample_db.sql. Only run this when intentionally re-vendoring
# the upstream dump — during normal development the sidecar must never
# drift from the dump it describes.
regen-sample-sha:
	@$(PYTHON) -c "import hashlib,pathlib; \
	p=pathlib.Path('examples/windows_sample_db/data/sample_db.sql'); \
	h=hashlib.sha256(p.read_bytes()).hexdigest(); \
	pathlib.Path('examples/windows_sample_db/data/sample_db.sql.sha256').write_text(f'{h}  sample_db.sql\n', encoding='ascii', newline='\n'); \
	print(h)"
