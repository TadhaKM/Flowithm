# Convenience wrappers around the pytest suite.
# All targets run from the company-brain/ directory.

.PHONY: test test-fast test-coverage test-watch

test:
	pytest tests/ -v --tb=short

test-fast:
	pytest tests/ -v --tb=short -m "not slow"

test-coverage:
	pytest tests/ --cov=brain --cov=api \
		--cov-report=term-missing \
		--cov-report=html:coverage_report

test-watch:
	pytest-watch tests/ -- -v --tb=short
