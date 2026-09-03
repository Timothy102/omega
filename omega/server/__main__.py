"""`python -m omega.server --port 7777` -- a manual entrypoint for the D1
daemon, ahead of the `omega serve` subcommand (wired up separately)."""
from __future__ import annotations

import argparse

from .app import main


def _cli() -> None:
    parser = argparse.ArgumentParser(prog="python -m omega.server")
    parser.add_argument("--port", type=int, default=7777)
    args = parser.parse_args()
    main(port=args.port)


if __name__ == "__main__":
    _cli()
