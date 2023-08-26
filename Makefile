# Makefile
MAX_LINE_LENGTH=79

lint:
	poetry run ruff .
	poetry run black --line-length $(MAX_LINE_LENGTH) . --check
	poetry run flake8 .

format:
	poetry run ruff . --fix
	poetry run black --line-length $(MAX_LINE_LENGTH) .
	poetry run isort .

docformat:
	PYTHON_FILES=$$(find . -name "*.py"); \
	for file in $$PYTHON_FILES; do \
		poetry run docformatter -i --force-wrap --wrap-summaries=$(MAX_LINE_LENGTH) --wrap-descriptions=$(MAX_LINE_LENGTH) $$file; \
	done
	poetry run ruff . --fix
	poetry run black --line-length $(MAX_LINE_LENGTH) .
	poetry run isort .

.PHONY: lint format docformat
