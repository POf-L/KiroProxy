#!/usr/bin/env python3
"""FastAPI mock server for debugging proxy/client behavior.

This is a development helper (not used by the main KiroProxy app).

Typical usage:
  python scripts/proxy_server.py --port 8000

Then point your client/proxy to `http://127.0.0.1:8000` and inspect requests:
  GET http://127.0.0.1:8000/logs
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from typing import Any, Dict, List

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("kiro_proxy.mock_server")

app = FastAPI(title="KiroProxy Mock Server")
request_log: List[Dict[str, Any]] = []


@app.middleware("http")
async def log_requests(request: Request, call_next):
    body = await request.body()

    request_log.append(
        {
            "timestamp": datetime.now().isoformat(),
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
            "headers": dict(request.headers),
            "body": body.decode("utf-8", errors="ignore")[:2000] if body else None,
        }
    )

    logger.info("%s %s", request.method, request.url.path)
    return await call_next(request)


@app.get("/")
async def root():
    return {"status": "ok", "message": "Mock server running", "requests_logged": len(request_log)}


@app.get("/logs")
async def get_logs(limit: int = 50):
    return {"total": len(request_log), "requests": request_log[-limit:]}


@app.get("/clear")
async def clear_logs():
    request_log.clear()
    return {"message": "Logs cleared"}


@app.api_route("/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])
async def mock_auth(_: Request, path: str):
    logger.info("Auth request: %s", path)
    return JSONResponse({"success": True, "token": "mock-token-for-testing", "expires_in": 3600})


@app.post("/v1/chat/completions")
async def mock_chat_completions(request: Request):
    body = await request.json()
    logger.info("Chat body preview: %s", json.dumps(body, ensure_ascii=False)[:500])

    return JSONResponse(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": int(datetime.now().timestamp()),
            "model": body.get("model", "mock-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Mock response from scripts/proxy_server.py"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
    )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])
async def catch_all(request: Request, path: str):
    logger.info("Caught: %s /%s", request.method, path)
    return JSONResponse(
        {
            "proxy_status": "intercepted",
            "method": request.method,
            "path": f"/{path}",
            "message": "Request intercepted by mock server",
            "headers_received": dict(request.headers),
        }
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KiroProxy mock server (dev helper)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
