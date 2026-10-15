# GridCast task runner. `make help` lists targets.
# All Python runs through uv against the committed lockfile (--locked fails if it is stale).

UV_RUN := uv run --locked

.DEFAULT_GOAL := help
.PHONY: help setup check lint format typecheck test hooks \
        data dq backtest results figures paper serve

help:  ## Show this help
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

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

# Targets below arrive with later milestones; they fail loudly until then.
data dq:  ## (M1) Ingest / data-quality report
	@echo "make $@: not implemented yet (milestone M1)"; exit 1
backtest results:  ## (M3) Rolling-origin backtest / results tables
	@echo "make $@: not implemented yet (milestone M3)"; exit 1
figures:  ## (M2) Regenerate paper figures
	@echo "make $@: not implemented yet (milestone M2)"; exit 1
paper:  ## (M7) Build the paper
	@echo "make $@: not implemented yet (milestone M7)"; exit 1
serve:  ## (M6) Run the API
	@echo "make $@: not implemented yet (milestone M6)"; exit 1
