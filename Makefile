# Fleet Integration Makefile

.PHONY: help list checkout verify test smoke coverage lint docker-up docker-down

help:
	@echo "Fleet Integration Commands:"
	@echo "  make list         - List all pinned components"
	@echo "  make checkout     - Clone/update all repos to pinned refs"
	@echo "  make verify       - Verify local refs match manifest"
	@echo "  make test         - Run contract tests"
	@echo "  make smoke        - Run integration smoke tests"
	@echo "  make coverage     - Check coverage across all repos"
	@echo "  make lint         - Run ruff + mypy on integration code"
	@echo "  make docker-up    - Start full fleet locally"
	@echo "  make docker-down  - Stop local fleet"

list:
	python3 scripts/resolve-components.py --list

checkout:
	python3 scripts/resolve-components.py --checkout

verify:
	python3 scripts/resolve-components.py --verify

test:
	pytest tests/test_contracts.py -v --tb=short

smoke:
	pytest tests/test_contracts.py -v -k smoke --tb=short

coverage:
	python3 scripts/check-coverage.py --threshold 75

lint:
	python3 -m ruff check scripts/ tests/
	python3 -m mypy scripts/ tests/ || true

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down -v
