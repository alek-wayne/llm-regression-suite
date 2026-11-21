"""Command-line entry points; all gate outcomes map to explicit process statuses."""

import argparse
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg

from .evidence import EXIT_CODE
from .reporting import index_entry, markdown, publish_report, write_json
from .runner import read_artifact, run_comparison
from .storage import database_url, import_comparison, migrate


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="evalsuite", description="Evaluate a policy-grounded assistant."
    )
    actions = cli.add_subparsers(dest="action", required=True)
    compare = actions.add_parser("compare", help="Run a paired comparison and enforce its gate")
    compare.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    compare.add_argument(
        "--scenario",
        choices=["repaired", "regressed"],
        default="repaired",
        help="Select the normal recordings or apply the seeded failure recordings",
    )
    compare.add_argument("--reference", type=Path, help="Baseline repository checkout")
    compare.add_argument("--case", action="append", help="Select a case ID; repeat to select more")
    compare.add_argument(
        "--trials", type=int, help="Repetitions per case and arm (fixture 1; live 3)"
    )
    compare.add_argument(
        "--budget-usd", type=Decimal, help="Required estimated spend ceiling in live mode"
    )
    compare.add_argument("--out", type=Path, default=Path("artifacts/latest"))
    replay = actions.add_parser(
        "replay", help="Recompute trial grades, metrics, and verdict without a key"
    )
    replay.add_argument("artifact", type=Path)
    replay.add_argument("--out", type=Path, help="Write verified JSON and Markdown")
    demo = actions.add_parser(
        "demo", help="Generate the two fixture reports and static console dataset"
    )
    demo.add_argument("--out", type=Path, default=Path("artifacts/demo"))
    demo.add_argument("--web-data", type=Path, default=Path("web/public/data"))
    actions.add_parser("migrate", help="Apply the PostgreSQL schema to DATABASE_URL")
    ingest = actions.add_parser("import", help="Verify and atomically import one or more reports")
    ingest.add_argument("artifacts", type=Path, nargs="+")
    serve = actions.add_parser(
        "serve", help="Serve the read-only PostgreSQL API and optional built UI"
    )
    serve.add_argument("--dist", type=Path)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = Path.cwd()
    try:
        if args.action == "compare":
            artifact = run_comparison(
                root,
                mode=args.mode,
                scenario=args.scenario,
                reference=args.reference,
                case_ids=args.case,
                trials=args.trials,
                budget=args.budget_usd,
            )
            publish_report(artifact, args.out)
            print(markdown(artifact))
            return EXIT_CODE[artifact.summary.verdict]
        if args.action == "replay":
            artifact = read_artifact(args.artifact)
            if args.out:
                publish_report(artifact, args.out)
            print(f"Verified {artifact.id}. All recorded grades and metrics reproduced.\n")
            print(markdown(artifact))
            return EXIT_CODE[artifact.summary.verdict]
        if args.action == "demo":
            entries = []
            for scenario, title, expected in (
                ("repaired", "Repaired candidate", "PASS"),
                ("regressed", "Seeded regression", "FAIL"),
            ):
                artifact = run_comparison(
                    root,
                    scenario=scenario,
                    title=title,
                )
                publish_report(artifact, args.out / scenario)
                if artifact.summary.verdict != expected:
                    raise ValueError(
                        f"The {scenario} demo returned {artifact.summary.verdict}; inspect {args.out / scenario}"
                    )
                write_json(
                    args.web_data / (artifact.id + ".json"), artifact.model_dump(mode="json")
                )
                entries.append({**index_entry(artifact), "scenario": scenario})
                print(
                    f"{scenario}: {expected} · {artifact.summary.case_count} cases · {args.out / scenario / 'report.md'}"
                )
            write_json(args.web_data / "index.json", {"comparisons": entries})
        elif args.action == "migrate":
            migrate(database_url())
            print("PostgreSQL schema ready.")
        elif args.action == "import":
            with psycopg.connect(database_url(), autocommit=True) as conn:
                for path in args.artifacts:
                    artifact = read_artifact(path)
                    inserted = import_comparison(conn, artifact)
                    print(f"{artifact.id}: {'imported' if inserted else 'already present'}")
        elif args.action == "serve":
            import uvicorn

            from .api import create_app

            uvicorn.run(create_app(args.dist), host=args.host, port=args.port)
        return 0
    except (ValueError, KeyError, OSError, InvalidOperation, psycopg.Error) as error:
        print(f"Evaluation unavailable: {error}", file=sys.stderr)
        return 2
