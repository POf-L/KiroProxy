#!/usr/bin/env python3
"""Kiro API Proxy 启动脚本"""
import sys

if __name__ == "__main__":
    args = sys.argv[1:]

    # 兼容旧的启动方式: python run.py [port]
    if not args:
        from kiro_proxy.main import run

        run(8080)
        raise SystemExit(0)

    if len(args) == 1:
        try:
            port = int(args[0])
        except ValueError:
            port = None
        else:
            from kiro_proxy.main import run

            run(port)
            raise SystemExit(0)

    # 默认使用 CLI 模式（支持 --help / 子命令等）
    from kiro_proxy.cli import main

    main()
