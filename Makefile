PY = uv run --locked python
SUITE = $(PY) -m evalsuite
DB_PORT ?= 5488
DEMO_DATABASE_URL = postgresql://eval:eval@127.0.0.1:$(DB_PORT)/evalsuite
PORT ?= 8765

.PHONY: check demo reports preview db-demo
check:
	uv sync --locked
	$(PY) -m ruff check src tests
	$(PY) -m pytest
	$(SUITE) compare --out artifacts/ci
	$(MAKE) demo

reports:
	$(SUITE) demo

demo: reports
	npm ci --prefix web
	npm run build --prefix web

preview: demo
	$(PY) -m http.server $(PORT) --bind 127.0.0.1 --directory web/dist

db-demo: reports
	DB_PORT=$(DB_PORT) docker compose up -d --wait db
	DATABASE_URL="$(DEMO_DATABASE_URL)" $(SUITE) migrate
	DATABASE_URL="$(DEMO_DATABASE_URL)" $(SUITE) import artifacts/demo/repaired/comparison.json artifacts/demo/regressed/comparison.json
	npm ci --prefix web
	VITE_API_BASE=/api npm run build --prefix web -- --outDir dist-db
	DATABASE_URL="$(DEMO_DATABASE_URL)" $(SUITE) serve --port $(PORT) --dist web/dist-db
