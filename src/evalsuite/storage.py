"""One transaction per import; PostgreSQL backs investigation, never the CI decision."""

import os
from importlib.resources import files

import psycopg
from psycopg.types.json import Jsonb

from .evidence import ARMS, Comparison, digest
from .runner import verify


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("Set DATABASE_URL for PostgreSQL access.")
    return url


def migrate(url: str) -> None:
    with psycopg.connect(url) as conn:
        # Two newly started importers must not create the same schema concurrently.
        conn.execute("SELECT pg_advisory_xact_lock(542390317)")
        conn.execute(files("evalsuite").joinpath("sql/001_initial.sql").read_text(encoding="utf-8"))


def import_comparison(conn: psycopg.Connection, artifact: Comparison) -> bool:
    verify(artifact)
    document = artifact.model_dump(mode="json")
    checksum = digest(document)
    with conn.transaction():
        inserted = conn.execute(
            """INSERT INTO comparisons (id, content_sha256, mode, verdict, artifact)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING RETURNING id""",
            (artifact.id, checksum, artifact.mode, artifact.summary.verdict, Jsonb(document)),
        ).fetchone()
        if inserted is None:
            existing = conn.execute(
                "SELECT content_sha256 FROM comparisons WHERE id=%s", (artifact.id,)
            ).fetchone()
            if existing is None or existing[0] != checksum:
                raise ValueError("Comparison ID already belongs to different evidence")
            return False
        trials, checks = [], []
        for case in artifact.cases:
            for arm in ARMS:
                for t in getattr(case, arm):
                    key = (artifact.id, case.id, arm, t.index)
                    trials.append(
                        (
                            *key,
                            t.status,
                            t.passed,
                            t.input_tokens,
                            t.output_tokens,
                            t.latency_ms,
                            t.cost_usd,
                        )
                    )
                    checks.extend((*key, a.name, a.passed, a.detail) for a in t.assertions)
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO trials (comparison_id,case_id,arm,trial_index,status,
                passed,input_tokens,output_tokens,latency_ms,cost_usd) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                trials,
            )
            cur.executemany(
                """INSERT INTO assertions (comparison_id,case_id,arm,trial_index,
                name,passed,detail) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                checks,
            )
    return True
