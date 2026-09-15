.PHONY: install test lint fixtures eval eval-ablation sandbox-image serve

install:
	pip install -e ".[dev]" numpy

test:
	python -m pytest -q

lint:
	ruff check reproagent tests

fixtures:
	python evals/fixtures/papers/fetch.py

eval:
	reproagent eval --configs full

eval-ablation:
	reproagent eval --configs full,no_repair,single_call,free_text_tools --workers 3

sandbox-image:
	docker build -t reproagent-sandbox:latest -f docker/sandbox.Dockerfile docker

serve:
	reproagent serve
