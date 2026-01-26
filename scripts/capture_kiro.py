#!/usr/bin/env python3
"""mitmproxy addon to capture Kiro IDE AWS requests/responses.

This is a development helper (not used by the main KiroProxy app).

Install:
  pip install mitmproxy

Run:
  mitmproxy --mode regular@8888 -s scripts/capture_kiro.py
or (no UI):
  mitmdump --mode regular@8888 -s scripts/capture_kiro.py

Then configure your Kiro IDE / system proxy to `127.0.0.1:8888` and install the
mitmproxy CA certificate if required (visit http://mitm.it).

Output:
  ./kiro_requests/*_request.json
  ./kiro_requests/*_response.json
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from mitmproxy import ctx, http

OUTPUT_DIR = "kiro_requests"
os.makedirs(OUTPUT_DIR, exist_ok=True)

request_count = 0


def request(flow: http.HTTPFlow) -> None:
    global request_count

    if "q.us-east-1.amazonaws.com" not in flow.request.host:
        return

    request_count += 1
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    request_data = {
        "timestamp": timestamp,
        "method": flow.request.method,
        "url": flow.request.url,
        "headers": dict(flow.request.headers),
        "body": None,
    }

    if flow.request.content:
        try:
            request_data["body"] = json.loads(flow.request.content.decode("utf-8"))
        except Exception:
            request_data["body_raw"] = flow.request.content.decode("utf-8", errors="replace")

    filename = f"{OUTPUT_DIR}/{timestamp}_{request_count:04d}_request.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(request_data, f, indent=2, ensure_ascii=False)

    ctx.log.info(f"[Kiro] Captured request #{request_count}: {flow.request.method} {flow.request.path}")


def response(flow: http.HTTPFlow) -> None:
    if "q.us-east-1.amazonaws.com" not in flow.request.host:
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    response_data = {
        "timestamp": timestamp,
        "status_code": flow.response.status_code,
        "headers": dict(flow.response.headers),
        "body": None,
    }

    if flow.response.content:
        try:
            response_data["body"] = json.loads(flow.response.content.decode("utf-8"))
        except Exception:
            response_data["body_raw_length"] = len(flow.response.content)
            response_data["body_preview"] = flow.response.content[:2000].decode("utf-8", errors="replace")

    filename = f"{OUTPUT_DIR}/{timestamp}_{request_count:04d}_response.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(response_data, f, indent=2, ensure_ascii=False)

    ctx.log.info(f"[Kiro] Captured response: {flow.response.status_code}")


if __name__ == "__main__":
    print(
        "This file is a mitmproxy addon.\n"
        "Run: mitmproxy --mode regular@8888 -s scripts/capture_kiro.py\n"
        "(or mitmdump --mode regular@8888 -s scripts/capture_kiro.py)"
    )
