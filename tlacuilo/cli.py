"""`tlacuilo schema` prints the contract; `check-schema` fails when the committed file
drifts from the models; `migrate` creates the job table."""

from __future__ import annotations

import argparse
import pathlib
import sys

from .contract import json_schema_text

SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "document-extraction.v1.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tlacuilo")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schema")
    sub.add_parser("check-schema")
    sub.add_parser("migrate")
    args = parser.parse_args(argv)
    if args.cmd == "schema":
        sys.stdout.write(json_schema_text())
        return 0
    if args.cmd == "check-schema":
        current = SCHEMA_PATH.read_text() if SCHEMA_PATH.exists() else ""
        if current != json_schema_text():
            print(f"contract drift: regenerate with `tlacuilo schema > {SCHEMA_PATH}`", file=sys.stderr)
            return 1
        print("contract in sync")
        return 0
    if args.cmd == "migrate":
        from .db import init_db

        init_db()
        print("jobs table ready")
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
