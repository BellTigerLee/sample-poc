"""Store bounded metric buffers, summaries, and a small HTML dashboard."""

# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# How to run:
# PORT=8080 BUFFER_SIZE=50 python3 src/store.py

import html
import json
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, TypeAlias

DEFAULT_PORT: Final = 8080
DEFAULT_BUFFER_SIZE: Final = 50
MAX_REQUEST_BYTES: Final = 1_048_576

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]


class InvalidPayloadError(TypeError):
    pass


class Store:
    """Mutable process-local state protected for threaded HTTP access."""

    def __init__(self, buffer_size: int) -> None:
        self._buffer_size = buffer_size
        self._buffers: dict[tuple[str, str], deque[JsonObject]] = {}
        self._summary: JsonObject = {"generatedAt": None, "summaries": []}
        self._lock = threading.Lock()

    def append(self, sample: JsonObject) -> None:
        key = (str(sample["site"]), str(sample["category"]))
        with self._lock:
            buffer = self._buffers.setdefault(key, deque(maxlen=self._buffer_size))
            buffer.append(sample)

    def buffers_document(self) -> JsonObject:
        with self._lock:
            buffers = [
                {"site": site, "category": category, "samples": list(samples)}
                for (site, category), samples in sorted(self._buffers.items())
            ]
        return {"bufferSize": self._buffer_size, "buffers": buffers}

    def set_summary(self, summary: JsonObject) -> None:
        with self._lock:
            self._summary = summary

    def summary_document(self) -> JsonObject:
        with self._lock:
            return dict(self._summary)


def parse_sample(document: JsonValue) -> JsonObject:
    if not isinstance(document, dict):
        raise InvalidPayloadError("sample must be a JSON object")
    site = document.get("site")
    category = document.get("category")
    value = document.get("value")
    timestamp = document.get("ts")
    node = document.get("node")
    if not isinstance(site, str) or not site:
        raise InvalidPayloadError("site must be a non-empty string")
    if not isinstance(category, str) or not category:
        raise InvalidPayloadError("category must be a non-empty string")
    if not isinstance(value, (int, float)):
        raise InvalidPayloadError("value must be numeric")
    if not isinstance(timestamp, (int, float)):
        raise InvalidPayloadError("ts must be numeric")
    if not isinstance(node, str):
        raise InvalidPayloadError("node must be a string")
    return {
        "site": site,
        "category": category,
        "value": float(value),
        "ts": float(timestamp),
        "node": node,
    }


def parse_summary(document: JsonValue) -> JsonObject:
    if not isinstance(document, dict):
        raise InvalidPayloadError("summary must be a JSON object")
    generated_at = document.get("generatedAt")
    summaries = document.get("summaries")
    if not isinstance(generated_at, (int, float)):
        raise InvalidPayloadError("generatedAt must be numeric")
    if not isinstance(summaries, list):
        raise InvalidPayloadError("summaries must be a list")
    return {"generatedAt": float(generated_at), "summaries": summaries}


def render_dashboard(store: Store) -> str:
    buffers = store.buffers_document()["buffers"]
    summary = store.summary_document().get("summaries", [])
    now = time.time()
    buffer_rows: list[str] = []
    site_last_seen: dict[str, float] = {}
    if isinstance(buffers, list):
        for buffer in buffers:
            if not isinstance(buffer, dict):
                continue
            site = str(buffer["site"])
            category = str(buffer["category"])
            samples = buffer["samples"]
            if not isinstance(samples, list):
                continue
            latest = samples[-1] if samples else None
            latest_value = "-"
            if isinstance(latest, dict):
                latest_value = f"{float(latest['value']):.2f}%"
                site_last_seen[site] = max(
                    site_last_seen.get(site, 0.0),
                    float(latest["ts"]),
                )
            buffer_rows.append(
                "<tr>"
                f"<td>{html.escape(site)}</td>"
                f"<td>{html.escape(category)}</td>"
                f"<td>{len(samples)}</td>"
                f"<td>{latest_value}</td>"
                "</tr>"
            )
    stale_rows = [
        "<li>"
        f"<strong>{html.escape(site)}</strong>"
        f"<span>last received {max(0, int(now - timestamp))} seconds ago</span>"
        "</li>"
        for site, timestamp in sorted(site_last_seen.items())
    ]
    summary_rows: list[str] = []
    if isinstance(summary, list):
        for item in summary:
            if not isinstance(item, dict):
                continue
            summary_rows.append(
                "<tr>"
                f"<td>{html.escape(str(item.get('site', '-')))}</td>"
                f"<td>{html.escape(str(item.get('category', '-')))}</td>"
                f"<td>{float(item.get('max', 0)):.2f}</td>"
                f"<td>{float(item.get('avg', 0)):.2f}</td>"
                f"<td>{float(item.get('min', 0)):.2f}</td>"
                "</tr>"
            )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Live multicluster CPU and memory metric buffers">
<title>ScaleX multicluster metrics</title>
<style>
:root {{ color-scheme: light dark; --bg:#f4f7f6; --surface:#ffffff; --text:#17211e;
  --muted:#596b65; --line:#ced9d5; --accent:#087f5b; --radius:12px; --space:16px; }}
@media (prefers-color-scheme:dark) {{ :root {{ --bg:#111816; --surface:#18221f;
  --text:#edf5f2; --muted:#a9bbb4; --line:#33463f; --accent:#63e6be; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text);
  font:15px/1.5 ui-sans-serif,system-ui,sans-serif; }}
main {{ inline-size:min(1120px,100%); margin-inline:auto; padding:clamp(20px,4vw,48px); }}
header {{ margin-block-end:32px; }}
h1 {{ margin:0 0 8px; font-size:clamp(28px,5vw,46px); letter-spacing:-0.04em; }}
p {{ margin:0; color:var(--muted); }}
.sites {{ display:flex; flex-wrap:wrap; gap:12px; padding:0; list-style:none; }}
.sites li {{ display:flex; gap:20px; justify-content:space-between; flex:1 1 260px;
  padding:14px 16px; border:1px solid var(--line); border-radius:var(--radius);
  background:var(--surface); }}
.sites span {{ color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(28rem,100%),1fr));
  gap:var(--space); margin-block-start:var(--space); }}
section {{ min-inline-size:0; padding:var(--space); border:1px solid var(--line);
  border-radius:var(--radius); background:var(--surface); overflow:auto; }}
h2 {{ margin:0 0 12px; font-size:18px; }}
table {{ inline-size:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }}
th,td {{ padding:10px 8px; text-align:start; border-block-end:1px solid var(--line); }}
th {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }}
.empty {{ padding:12px 0; color:var(--muted); }}
</style>
</head>
<body><main>
<header><h1>Multicluster metrics</h1><p>Recent node samples from b and c, retained in bounded memory.</p></header>
<ul class="sites">{''.join(stale_rows) or '<li class="empty">Waiting for samples</li>'}</ul>
<div class="grid">
<section><h2>Ring buffers</h2><table><thead><tr><th>Site</th><th>Metric</th>
<th>Samples</th><th>Latest</th></tr></thead><tbody>{''.join(buffer_rows)}</tbody></table>
{'' if buffer_rows else '<p class="empty">No samples received yet.</p>'}</section>
<section><h2>Latest summary</h2><table><thead><tr><th>Site</th><th>Metric</th>
<th>Max</th><th>Avg</th><th>Min</th></tr></thead><tbody>{''.join(summary_rows)}</tbody></table>
{'' if summary_rows else '<p class="empty">No analysis received yet.</p>'}</section>
</div></main></body></html>"""


def make_handler(store: Store) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send_payload(
            self,
            status: int,
            payload: bytes,
            content_type: str,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def send_json(self, status: int, document: JsonObject) -> None:
            self.send_payload(
                status,
                (json.dumps(document, separators=(",", ":")) + "\n").encode(),
                "application/json",
            )

        def read_json(self) -> JsonValue:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise InvalidPayloadError("invalid Content-Length")
            return json.loads(self.rfile.read(length))

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path == "/healthz":
                self.send_payload(200, b"ok\n", "text/plain; charset=utf-8")
                return
            if self.path == "/samples":
                self.send_json(200, store.buffers_document())
                return
            if self.path == "/summary":
                self.send_json(200, store.summary_document())
                return
            if self.path == "/":
                self.send_payload(
                    200,
                    render_dashboard(store).encode(),
                    "text/html; charset=utf-8",
                )
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            try:
                document = self.read_json()
                if self.path == "/samples":
                    store.append(parse_sample(document))
                    self.send_json(202, {"status": "accepted"})
                    return
                if self.path == "/summary":
                    store.set_summary(parse_summary(document))
                    self.send_json(202, {"status": "accepted"})
                    return
                self.send_error(404)
            except (
                json.JSONDecodeError,
                InvalidPayloadError,
                KeyError,
                ValueError,
            ) as error:
                self.send_json(400, {"error": str(error)})

        def log_message(self, _format: str, *_args: str) -> None:
            return

    return Handler


def main() -> None:
    buffer_size = int(os.environ.get("BUFFER_SIZE", str(DEFAULT_BUFFER_SIZE)))
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(Store(buffer_size)))
    server.serve_forever()


if __name__ == "__main__":
    main()
