import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from evalsuite.api import create_app
from evalsuite.runner import run_comparison
from evalsuite.storage import import_comparison, migrate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database_schema(monkeypatch):
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.skip("Set TEST_DATABASE_URL for PostgreSQL integration tests")
    schema = sql.Identifier("evaltest_" + uuid4().hex)
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        url = make_conninfo(base, options=f"-c search_path={schema.as_string(admin)}")
        monkeypatch.setenv("DATABASE_URL", url)
        try:
            yield url
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema))


@pytest.fixture
def database(database_schema):
    migrate(database_schema)
    return database_schema


def test_postgres_atomic_concurrent_import_and_read_only_views(database, project):
    artifact = run_comparison(project, scenario="regressed")

    def ingest(_):
        with psycopg.connect(database, autocommit=True) as conn:
            return import_comparison(conn, artifact)

    with ThreadPoolExecutor(max_workers=2) as pool:
        inserted = list(pool.map(ingest, range(2)))
    assert sorted(inserted) == [False, True]
    migrate(database)
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM comparisons").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM trials").fetchone()[0] == 96
        assert conn.execute("SELECT count(*) FROM assertions").fetchone()[0] == 480
        p95, cost, count = conn.execute("""
            SELECT p95_ms,known_cost_usd,passing_trials FROM arm_usage WHERE arm='candidate'
        """).fetchone()
        assert p95 == pytest.approx(artifact.summary.candidate_p95_ms)
        assert cost == Decimal(artifact.summary.candidate_cost_usd)
        assert count == 40

    with TestClient(create_app()) as client:
        history = client.get("/api/comparisons").json()["comparisons"]
        assert history[0]["delta"] == artifact.summary.delta
        assert history[0]["id"] == artifact.id
        link = f"/api/comparisons/{artifact.id}"
        assert client.get(link).json() == artifact.model_dump(mode="json")
        case = client.get(link + "/cases/CMP-02").json()
        assert case["candidate"][0]["guardrail"] == "withheld_unsupported_wording"
        assert client.post(link, json={}).status_code == 405
        assert client.get(link + "/cases/unknown").status_code == 404
        assert client.get("/api/comparisons/missing").status_code == 404


def test_mid_import_constraint_failure_rolls_back_every_table(database):
    artifact = run_comparison(ROOT, case_ids=["BIL-04", "CMP-02"])
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(
            "ALTER TABLE trials ADD CONSTRAINT fail_second_case CHECK (case_id <> 'CMP-02')"
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            import_comparison(conn, artifact)
        counts = conn.execute("""
            SELECT (SELECT count(*) FROM comparisons), (SELECT count(*) FROM trials),
                   (SELECT count(*) FROM assertions)
        """).fetchone()
        assert counts == (0, 0, 0)
        conn.execute("ALTER TABLE trials DROP CONSTRAINT fail_second_case")
        assert import_comparison(conn, artifact)
        conn.execute("UPDATE comparisons SET content_sha256=repeat('0',64)")
        with pytest.raises(ValueError, match="different evidence"):
            import_comparison(conn, artifact)


def test_api_distinguishes_no_database_from_empty_history(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(create_app()) as client:
        assert client.get("/api/comparisons").status_code == 503


def test_api_requests_migration_for_a_reachable_database_without_schema(database_schema):
    history_path = "/api/comparisons"
    missing_comparison = history_path + "/unknown"
    missing_case = missing_comparison + "/cases/CMP-02"
    with TestClient(create_app()) as client:
        for path in (history_path, missing_comparison, missing_case):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json() == {"detail": "Comparison storage requires evalsuite migrate"}

        migrate(database_schema)
        history = client.get(history_path)
        assert history.status_code == 200
        assert history.json() == {"comparisons": []}
        assert client.get(missing_comparison).status_code == 404
        assert client.get(missing_case).status_code == 404
