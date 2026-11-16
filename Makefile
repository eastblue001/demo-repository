.PHONY: install test lint run docker-build docker-run

install:
	python -m pip install -e '.[dev]'

test:
	pytest

lint:
	ruff check src tests
	ruff format --check src tests

run:
	python -m yahoo_finance_mcp.server

docker-build:
	docker build -t yahoo-finance-mcp:local .

docker-run:
	docker run --rm -p 8000:8000 --env-file .env yahoo-finance-mcp:local
