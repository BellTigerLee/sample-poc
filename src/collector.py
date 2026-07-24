"""Collect host CPU and memory utilization and send samples to ring-store."""

# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# How to run:
# STORE_URL=http://127.0.0.1:8080 SITE_NAME=b python3 src/collector.py

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, TypedDict

DEFAULT_PORT: Final = 8080
DEFAULT_INTERVAL_SECONDS: Final = 5.0
REQUEST_TIMEOUT_SECONDS: Final = 3.0


class CpuSnapshot(TypedDict):
    idle: int
    total: int


class Sample(TypedDict):
    site: str
    category: str
    value: float
    ts: float
    node: str


def read_cpu_snapshot() -> CpuSnapshot:
    with open("/proc/stat", encoding="utf-8") as stat_file:
        fields = stat_file.readline().split()
    values = [int(value) for value in fields[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return {"idle": idle, "total": sum(values)}


def cpu_busy_percent(previous: CpuSnapshot, current: CpuSnapshot) -> float:
    total_delta = current["total"] - previous["total"]
    idle_delta = current["idle"] - previous["idle"]
    if total_delta <= 0:
        return 0.0
    return round(100.0 * (total_delta - idle_delta) / total_delta, 2)


def memory_used_percent() -> float:
    memory: dict[str, int] = {}
    with open("/proc/meminfo", encoding="utf-8") as meminfo:
        for line in meminfo:
            key, value = line.split(":", maxsplit=1)
            memory[key] = int(value.split()[0])
    total = memory["MemTotal"]
    available = memory["MemAvailable"]
    return round(100.0 * (total - available) / total, 2)


def post_sample(store_url: str, sample: Sample) -> None:
    payload = json.dumps(sample).encode()
    request = urllib.request.Request(
        f"{store_url.rstrip('/')}/samples",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        response.read()


def collect_forever(store_url: str, site: str, node: str, interval: float) -> None:
    previous = read_cpu_snapshot()
    while True:
        time.sleep(interval)
        current = read_cpu_snapshot()
        timestamp = time.time()
        samples: tuple[Sample, ...] = (
            {
                "site": site,
                "category": "cpu",
                "value": cpu_busy_percent(previous, current),
                "ts": timestamp,
                "node": node,
            },
            {
                "site": site,
                "category": "mem",
                "value": memory_used_percent(),
                "ts": timestamp,
                "node": node,
            },
        )
        previous = current
        for sample in samples:
            try:
                post_sample(store_url, sample)
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                print(
                    f"{datetime.now(timezone.utc).isoformat()} sample delivery failed: {error}",
                    flush=True,
                )


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
    site = os.environ.get("SITE_NAME", "unknown")
    node = os.environ.get("NODE_NAME", "unknown")
    interval = float(os.environ.get("COLLECT_INTERVAL", str(DEFAULT_INTERVAL_SECONDS)))
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    worker = threading.Thread(
        target=collect_forever,
        args=(store_url, site, node, interval),
        daemon=True,
    )
    worker.start()
    ThreadingHTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


if __name__ == "__main__":
    main()
