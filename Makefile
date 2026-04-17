# Makefile — enforces the 8-level test pyramid.
#
# Each level can only run if the previous level's marker file exists in
# .test-state/. Markers are written on green, deleted on any source change.
#
# Usage:
#   make test-1   # unit
#   make test-2   # property (requires .test-state/level1)
#   make test-3   # parity   (requires .test-state/level2)
#   ...
#   make test-all # walks 1..6 sequentially, stops on first failure
#   make paper-soak  # level-7 (interactive)
#   make shadow      # level-8 (interactive, multi-day)

PY = uv run
STATE = .test-state
$(shell mkdir -p $(STATE))

.PHONY: test-1 test-2 test-3 test-4 test-5 test-6 test-all paper-soak shadow clean-state lint typecheck test fmt coverage doctor check help

# Source-change watcher: any .py edit invalidates all gates
SRC_HASH := $(shell find src tests/level_1_unit -name '*.py' -type f -exec md5sum {} \; 2>/dev/null | sort | md5sum | awk '{print $$1}')
HASH_FILE := $(STATE)/src.hash

$(HASH_FILE):
	@echo "$(SRC_HASH)" > $(HASH_FILE)
	@rm -f $(STATE)/level*

ifneq ($(shell cat $(HASH_FILE) 2>/dev/null),$(SRC_HASH))
$(shell echo "$(SRC_HASH)" > $(HASH_FILE); rm -f $(STATE)/level*)
endif

test-1: $(HASH_FILE)
	$(PY) pytest tests/level_1_unit -v -m level1 && touch $(STATE)/level1

test-2: $(STATE)/level1
	$(PY) pytest tests/level_2_property -v -m level2 && touch $(STATE)/level2

test-3: $(STATE)/level2
	$(PY) pytest tests/level_3_parity -v -m level3 && touch $(STATE)/level3

test-4: $(STATE)/level3
	$(PY) pytest tests/level_4_replay -v -m level4 && touch $(STATE)/level4

test-5: $(STATE)/level4
	$(PY) pytest tests/level_5_integration -v -m level5 && touch $(STATE)/level5

test-6: $(STATE)/level5
	$(PY) pytest tests/level_6_chaos -v -m level6 && touch $(STATE)/level6

test-all: test-1 test-2 test-3 test-4 test-5 test-6
	@echo
	@echo "===================================================="
	@echo " Levels 1-6 GREEN. Paper soak (level 7) unlocked."
	@echo " Run: make paper-soak"
	@echo "===================================================="

paper-soak: $(STATE)/level6
	@echo "Level 7: paper soak (2+ weeks). See docs/TESTING.md."
	$(PY) -m mnq.cli.main soak start --target-days 14

shadow: $(STATE)/level6
	@echo "Level 8: shadow validation (30+ days, real strategy in shadow)."
	$(PY) -m mnq.cli.main shadow start --strategy specs/strategies/v0_1_baseline.yaml

lint:
	$(PY) ruff check src tests
	$(PY) ruff format --check src tests

fmt:
	$(PY) ruff format src tests
	$(PY) ruff check --fix src tests

typecheck:
	$(PY) mypy src

test:
	$(PY) pytest tests -q

coverage:
	COVERAGE_FILE=/tmp/mnq-coverage $(PY) pytest tests --cov=mnq --cov-report=term-missing --cov-branch -q

# Fast pre-push bundle: lint + typecheck + unit tests. Green here ≈ safe to push.
check: lint typecheck test-1
	@echo "== pre-push check GREEN =="

doctor:
	$(PY) python -m mnq.cli.main doctor

help:
	@echo "Primary targets:"
	@echo "  make check      — lint + typecheck + level-1 tests (fast pre-push)"
	@echo "  make test       — run the entire test suite"
	@echo "  make test-N     — run level-N tests (N=1..6); each requires previous level green"
	@echo "  make test-all   — walk 1..6 sequentially"
	@echo "  make coverage   — full-suite coverage report"
	@echo "  make lint       — ruff check + format --check"
	@echo "  make fmt        — ruff format + --fix"
	@echo "  make typecheck  — mypy src"
	@echo "  make doctor     — run \`mnq doctor\` health check"
	@echo "  make clean-state — clear .test-state (forces full re-run)"

clean-state:
	rm -rf $(STATE)
