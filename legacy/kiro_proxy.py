#!/usr/bin/env python3
"""Legacy entrypoint for KiroProxy.

Deprecated: prefer `python run.py` or the installed CLI `kiro-proxy`.

This wrapper is kept for older guides that run:
  python legacy/kiro_proxy.py [port]
"""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        from kiro_proxy.main import run

        run(int(sys.argv[1]))
        return

    from kiro_proxy.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    print("[Legacy] `legacy/kiro_proxy.py` 已弃用，推荐使用：`python run.py` 或 `kiro-proxy`")
    main()
