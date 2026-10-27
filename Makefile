.PHONY: install run test lint benchmark app smoke all

PY ?= python3

install:
	$(PY) -m pip install -r requirements-dev.txt -r requirements-app.txt

run:
	$(PY) scripts/run_pipeline.py

# redirect instead of `| tee`: in POSIX sh a pipe returns tee's status and would hide pytest / --cov-fail-under failures
test:
	@$(PY) -m pytest --cov=customer_master --cov-report=term-missing:skip-covered --cov-fail-under=85 -q > outputs/coverage.txt 2>&1; rc=$$?; cat outputs/coverage.txt; exit $$rc

lint:
	ruff check src tests scripts app

benchmark:
	$(PY) scripts/benchmark_scale.py

app:
	$(PY) -m streamlit run app/streamlit_app.py

# fast check that the dashboard sections render and the review/impact logic works (no full pipeline)
smoke:
	$(PY) -m pytest -q tests/test_app_smoke.py tests/test_dashboard_data.py tests/test_review_store.py tests/test_impact_scenario.py

# lint, tests, pipeline (includes the README-vs-generated-files check), then monitoring
all: lint test run
	$(PY) scripts/monitor_quality.py
