"""Run with: python -m api  (default port 8000, host 127.0.0.1)."""
from __future__ import annotations

import argparse

import uvicorn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="api", description="Trading Dashboard API server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true",
                   help="Enable hot reload (development only)")
    args = p.parse_args(argv)

    uvicorn.run("api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
