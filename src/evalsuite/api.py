"""Read-only HTTP views of imported comparison evidence."""

from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .storage import database_url

STORAGE_UNAVAILABLE = "Comparison storage is unavailable"


def read_connection() -> Iterator[psycopg.Connection]:
    try:
        conn = psycopg.connect(database_url(), connect_timeout=4)
        conn.read_only = True
    except (psycopg.Error, ValueError):
        raise HTTPException(503, STORAGE_UNAVAILABLE) from None
    try:
        with conn:
            try:
                yield conn
            except psycopg.errors.UndefinedTable:
                conn.rollback()
                if conn.execute("SELECT to_regclass('comparisons')").fetchone() != (None,):
                    raise
                raise HTTPException(503, "Comparison storage requires evalsuite migrate") from None
    except psycopg.OperationalError:
        raise HTTPException(503, STORAGE_UNAVAILABLE) from None


ReadConnection = Annotated[psycopg.Connection, Depends(read_connection, scope="function")]


def create_app(dist: Path | None = None) -> FastAPI:
    app = FastAPI(title="LLM Regression Suite", version=version("llm-regression-suite"))

    @app.get("/api/comparisons")
    def history(conn: ReadConnection) -> dict:
        rows = conn.execute("""
            SELECT id, artifact->>'title', mode, verdict, artifact->'summary'->'delta',
                   (artifact->'summary'->>'case_count')::integer
            FROM comparisons ORDER BY imported_at DESC, id
        """).fetchall()
        return {
            "comparisons": [
                {
                    "id": row[0],
                    "title": row[1],
                    "mode": row[2],
                    "verdict": row[3],
                    "delta": row[4],
                    "case_count": row[5],
                    "artifact": row[0] + ".json",
                }
                for row in rows
            ]
        }

    @app.get("/api/comparisons/{comparison_id}")
    def comparison(comparison_id: str, conn: ReadConnection) -> dict:
        row = conn.execute(
            "SELECT artifact FROM comparisons WHERE id=%s", (comparison_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Comparison not found")
        return row[0]

    @app.get("/api/comparisons/{comparison_id}/cases/{case_id}")
    def case(comparison_id: str, case_id: str, conn: ReadConnection) -> dict:
        row = conn.execute(
            """
            SELECT c FROM comparisons, LATERAL jsonb_array_elements(artifact->'cases') c
            WHERE comparisons.id=%s AND c->>'id'=%s
        """,
            (comparison_id, case_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Case not found")
        return row[0]

    if dist is not None:
        app.mount("/", StaticFiles(directory=dist, html=True), name="console")
    return app
