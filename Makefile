# GridCast task runner. `make help` lists targets.
# All Python runs through uv against the committed lockfile (--locked fails if it is stale).

UV_RUN := uv run --locked

-include .env  # GRIDCAST_SERVER etc. (git-ignored)
SSH := ssh -i $(HOME)/.ssh/gridcast_deploy $(GRIDCAST_SERVER)

.DEFAULT_GOAL := help
.PHONY: help setup check lint format typecheck test hooks \
        collect gaps collector-install collector-uninstall \
        server-bootstrap server-status server-gaps \
        data dq backtest results figures paper serve

help:  ## Show this help
	@grep -E '^[a-z][a-z -]*:.*## ' $(firstword $(MAKEFILE_LIST)) | awk -F':.*## ' '{printf "  %-20s %s\n", $$1, $$2}'

setup:  ## Create the environment and install git hooks
	uv sync --locked
	$(UV_RUN) pre-commit install

check: lint typecheck test  ## Lint, type-check and test (what CI runs)

lint:  ## Ruff lint + format check
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .

format:  ## Apply ruff fixes and formatting
	$(UV_RUN) ruff check --fix .
	$(UV_RUN) ruff format .

typecheck:  ## mypy --strict on src/
	$(UV_RUN) mypy

test:  ## pytest with coverage gate (network disabled)
	$(UV_RUN) pytest

hooks:  ## Run every pre-commit hook (incl. gitleaks) on all files
	$(UV_RUN) pre-commit run --all-files

collect:  ## (M1-T3) Run one collector pass now, ignoring cadence (needs network)
	$(UV_RUN) gridcast collect --force

gaps:  ## (M1-T3) Live-collector gap report for the last 7 days
	$(UV_RUN) gridcast gaps --days 7

collector-install:  ## (M1-T3) Install the hourly, AC-power-only LaunchAgent (macOS)
	scripts/collector/install.sh

collector-uninstall:  ## Remove the LaunchAgent (keeps collected data)
	scripts/collector/uninstall.sh

server-bootstrap:  ## Set up / update the production server (idempotent)
	$(SSH) 'bash -s' < scripts/server/bootstrap.sh

server-status:  ## Collector timer state and latest log lines on the server
	$(SSH) 'systemctl list-timers gridcast-collector.timer --no-pager | head -2; journalctl -u gridcast-collector.service --no-pager -o cat -n 5'

server-gaps:  ## Gap report from the production collector
	$(SSH) 'cd gridcast && .venv/bin/gridcast gaps --days 7'

# Targets below arrive with later milestones; they fail loudly until then.
data dq:  ## (M1) Silver tables / data-quality report
	@echo "make $@: not implemented yet (milestone M1)"; exit 1
backtest results:  ## (M3) Rolling-origin backtest / results tables
	@echo "make $@: not implemented yet (milestone M3)"; exit 1
figures:  ## (M2) Regenerate paper figures
	@echo "make $@: not implemented yet (milestone M2)"; exit 1
paper:  ## (M7) Build the paper
	@echo "make $@: not implemented yet (milestone M7)"; exit 1
serve:  ## (M6) Run the API
	@echo "make $@: not implemented yet (milestone M6)"; exit 1
