.PHONY: setup serve test bench bench-readme clean

VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

setup:  ## Create venv and install dependencies
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

serve:  ## Run the API server
	$(VENV)/bin/uvicorn llm_gateway.app:app --reload

test:  ## Run the test suite
	$(PY) -m pytest

bench:  ## Run both benchmarks and print markdown tables
	$(PY) -m benchmarks.routing_benchmark
	@echo ""
	$(PY) -m benchmarks.scheduler_benchmark

bench-readme:  ## Run benchmarks and write the numbers into README.md
	$(PY) scripts/update_readme_benchmarks.py

clean:
	rm -rf $(VENV) .pytest_cache **/__pycache__ *.egg-info
