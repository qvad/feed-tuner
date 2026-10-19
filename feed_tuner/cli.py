from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
import sys
from .config import load
from .browser import login, run


def main() -> int:
    parser = argparse.ArgumentParser(description="Experimental topic browsing for X and Threads. Browsing can affect recommendations even without --apply.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("login", "run"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", type=Path, default=Path("config.toml"))
        if name == "login":
            sub.add_argument("platform", choices=("x", "threads"))
        else:
            sub.add_argument("--apply", action="store_true", help="Open extracted matching posts; default only searches, scrolls, and ranks")
            sub.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    try:
        config = load(args.config)
        if args.command == "login":
            asyncio.run(login(config, args.platform))
        else:
            print("Compatibility is experimental. Search browsing affects account signals. Extracted snippets and topics are sent to your configured model.", flush=True)
            asyncio.run(run(config, args.apply, args.headless))
    except KeyboardInterrupt:
        print("Stopped by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Network/provider exceptions can contain URLs or server-supplied data.
        message = str(exc) if isinstance(exc, ValueError) or type(exc).__name__ == "StopBrowsing" else type(exc).__name__
        print(f"Stopped: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
