"""Calculate per-site metric summaries from ring-store samples."""

# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# How to run:
# STORE_URL=http://127.0.0.1:8080 python3 src/analyzer.py

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, TypeAlias

DEFAULT_PORT: Final = 8080
DEFAULT_INTERVAL_SECONDS: Final = 10.0
REQUEST_TIMEOUT_SECONDS: Final = 3.0

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]


class InvalidStoreResponseError(TypeError):
    pass


def read_buffers(store_url: str) -> list[JsonObject]:
    with urllib.request.urlopen(
        f"{store_url.rstrip('/')}/samples",
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as response:
        document = json.load(response)
    if not isinstance(document, dict) or not isinstance(document.get("buffers"), list):
        raise InvalidStoreResponseError(
            "ring-store returned an invalid samples document"
        )
    return document["buffers"]


def summarize(buffers: list[JsonObject]) -> list[JsonObject]:
    summaries: list[JsonObject] = []
    for buffer in buffers:
        site = buffer.get("site")
        category = buffer.get("category")
        samples = buffer.get("samples")
        if not isinstance(site, str) or not isinstance(category, str):
            continue
        if not isinstance(samples, list):
            continue
        values = [
            float(sample["value"])
            for sample in samples
            if isinstance(sample, dict) and isinstance(sample.get("value"), (int, float))
        ]
        if not values:
            continue
        summaries.append(
            {
                "site": site,
                "category": category,
                "count": len(values),
                "max": round(max(values), 2),
                "avg": round(sum(values) / len(values), 2),
                "min": round(min(values), 2),
            }
        )
    return summaries


def post_summary(store_url: str, summaries: list[JsonObject]) -> None:
    payload = json.dumps(
        {"generatedAt": time.time(), "summaries": summaries}
    ).encode()
    request = urllib.request.Request(
        f"{store_url.rstrip('/')}/summary",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        response.read()


def analyze_forever(store_url: str, interval: float) -> None:
    while True:
        try:
            post_summary(store_url, summarize(read_buffers(store_url)))
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            InvalidStoreResponseError,
        ) as error:
            print(
                f"{datetime.now(timezone.utc).isoformat()} analysis failed: {error}",
                flush=True,
            )
        time.sleep(interval)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path != "/healthz":
            self.send_error(404)
            return
        payload = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: str) -> None:
        return


def main() -> None:
    store_url = os.environ.get("STORE_URL", "http://127.0.0.1:8081")
    interval = float(os.environ.get("ANALYZE_INTERVAL", str(DEFAULT_INTERVAL_SECONDS)))
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    worker = threading.Thread(
        target=analyze_forever,
        args=(store_url, interval),
        daemon=True,
    )
    worker.start()
    ThreadingHTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


if __name__ == "__main__":
    main()
