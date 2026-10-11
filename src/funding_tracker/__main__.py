"""Command line: ``python -m funding_tracker <command>``.

    update    read new reports from the sources, then rebuild every output (the weekly job)
    collect   only read the sources
    build     only rebuild rounds, checks and the workbook from the records on file
    backfill  re-read everything since the start date
    reparse   re-run the parsing rules on pages already downloaded (no network)
"""
from __future__ import annotations

import argparse
import logging

from . import pipeline


def main() -> None:
    parser = argparse.ArgumentParser(prog="funding_tracker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["update", "collect", "build", "backfill", "reparse"])
    parser.add_argument("--quiet", action="store_true", help="only print warnings")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    if args.command == "collect":
        pipeline.collect()
    elif args.command == "build":
        pipeline.build()
    elif args.command == "update":
        pipeline.collect()
        pipeline.build()
    elif args.command == "backfill":
        pipeline.collect(full=True)
        pipeline.build()
    elif args.command == "reparse":
        pipeline.collect(full=True, offline=True)
        pipeline.build(offline=True)


if __name__ == "__main__":
    main()
