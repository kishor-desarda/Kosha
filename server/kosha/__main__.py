# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the Kosha server:  python -m kosha  [--demo] [--host 0.0.0.0] [--port 8080]"""
from __future__ import annotations

import argparse
import logging

import uvicorn

from . import config


def main() -> None:
    p = argparse.ArgumentParser(prog="kosha", description="Kosha PLM server")
    p.add_argument("--host", default=config.HOST)
    p.add_argument("--port", type=int, default=config.PORT)
    p.add_argument("--demo", action="store_true", help="load demo users, folders, drawings and BOMs first")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.demo:
        from .app import StartupError, bootstrap
        from .seed import seed
        try:
            bootstrap()
        except StartupError as e:
            raise SystemExit(f"Kosha did not start: {e}")
        seed()
    uvicorn.run("kosha.app:app", host=args.host, port=args.port, limit_concurrency=200,
                timeout_keep_alive=30, log_level="info")


if __name__ == "__main__":
    main()
