.PHONY: install test test-all lint demo demo-cache demo-schema dashboard docker-image clean

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest tests -q -m "not docker"

test-all:
	$(PYTHON) -m pytest tests -q

lint:
	ruff check repomedic tests

demo: demo-cache demo-schema

demo-cache:
	repomedic investigate fixtures/cache-bug --executor local

demo-schema:
	repomedic investigate fixtures/schema-mismatch --executor local

dashboard:
	repomedic dashboard --repo fixtures/cache-bug

docker-image:
	docker build -t repomedic .

clean:
	rm -rf .pytest_cache .ruff_cache dist build
	rm -rf fixtures/cache-bug/.repomedic fixtures/schema-mismatch/.repomedic
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
