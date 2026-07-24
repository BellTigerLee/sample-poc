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


CHART_W: Final = 720
CHART_H: Final = 240
PAD_L: Final = 40
PAD_R: Final = 56
PAD_T: Final = 16
PAD_B: Final = 28
SITE_CLASS: Final = {"b": "s-b", "c": "s-c"}
CHART_SITES: Final = ("b", "c")


def _svg_line_chart(title: str, series: dict[str, list[JsonObject]], now: float) -> str:
    """Server-rendered inline SVG line chart, 0-100%, one line per site."""
    plot_w = CHART_W - PAD_L - PAD_R
    plot_h = CHART_H - PAD_T - PAD_B

    # Ceiling = highest sample across both series + 10 headroom, capped at 100.
    all_values = [
        float(sample["value"])
        for site in CHART_SITES
        for sample in (series.get(site) or [])
    ]
    ceiling = min(100.0, max(all_values) + 10.0) if all_values else 100.0

    def py(value: float) -> float:
        value = max(0.0, min(ceiling, value))
        return PAD_T + plot_h * (1.0 - value / ceiling)

    def px(index: int, count: int) -> float:
        if count <= 1:
            return PAD_L
        return PAD_L + plot_w * index / (count - 1)

    parts: list[str] = []
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        gridline = ceiling * fraction
        gy = py(gridline)
        parts.append(
            f'<line class="grid" x1="{PAD_L}" y1="{gy:.1f}" '
            f'x2="{PAD_L + plot_w:.1f}" y2="{gy:.1f}"/>'
        )
        parts.append(
            f'<text class="axis" x="{PAD_L - 7}" y="{gy + 3.5:.1f}" '
            f'text-anchor="end">{gridline:.0f}</text>'
        )

    drew_any = False
    for site in CHART_SITES:
        samples = series.get(site) or []
        if not samples:
            continue
        drew_any = True
        css = SITE_CLASS[site]
        count = len(samples)
        points: list[str] = []
        marks: list[str] = []
        for index, sample in enumerate(samples):
            value = float(sample["value"])
            x = px(index, count)
            y = py(value)
            points.append(f"{x:.1f},{y:.1f}")
            ago = max(0, int(now - float(sample["ts"])))
            marks.append(
                f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="7">'
                f"<title>{html.escape(site)} · {value:.2f}% · {ago}s ago</title>"
                "</circle>"
                f'<circle class="dot {css}" cx="{x:.1f}" cy="{y:.1f}" r="2.2"/>'
            )
        parts.append(f'<polyline class="line {css}" points="{" ".join(points)}"/>')
        parts.extend(marks)
        last = samples[-1]
        lx = px(count - 1, count)
        ly = py(float(last["value"]))
        parts.append(
            f'<text class="endlab" x="{lx + 8:.1f}" y="{ly + 3.5:.1f}">'
            f'{site} {float(last["value"]):.0f}%</text>'
        )

    if not drew_any:
        parts.append(
            f'<text class="axis" x="{CHART_W / 2:.0f}" y="{CHART_H / 2:.0f}" '
            'text-anchor="middle">waiting for samples</text>'
        )

    legend = "".join(
        f'<span class="lg"><span class="sw {SITE_CLASS[s]}"></span>{s}</span>'
        for s in CHART_SITES
    )
    return (
        '<figure class="chart-card">'
        f"<figcaption>{html.escape(title)}"
        f'<span class="legend">{legend}</span></figcaption>'
        f'<svg viewBox="0 0 {CHART_W} {CHART_H}" class="chart" '
        f'role="img" aria-label="{html.escape(title)} over time for b and c">'
        f'{"".join(parts)}</svg></figure>'
    )


def render_dashboard(store: Store) -> str:
    buffers = store.buffers_document()["buffers"]
    summary = store.summary_document().get("summaries", [])
    now = time.time()
    buffer_rows: list[str] = []
    site_last_seen: dict[str, float] = {}
    by_key: dict[tuple[str, str], list[JsonObject]] = {}
    if isinstance(buffers, list):
        for buffer in buffers:
            if not isinstance(buffer, dict):
                continue
            site = str(buffer["site"])
            category = str(buffer["category"])
            samples = buffer["samples"]
            if not isinstance(samples, list):
                continue
            by_key[(site, category)] = samples
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
    charts_html = "".join(
        _svg_line_chart(
            title,
            {site: by_key.get((site, category), []) for site in CHART_SITES},
            now,
        )
        for category, title in (("cpu", "CPU %"), ("mem", "Memory %"))
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
  --muted:#596b65; --line:#ced9d5; --accent:#087f5b; --radius:12px; --space:16px;
  --series-b:#1c7ed6; --series-c:#e8590c; }}
@media (prefers-color-scheme:dark) {{ :root {{ --bg:#111816; --surface:#18221f;
  --text:#edf5f2; --muted:#a9bbb4; --line:#33463f; --accent:#63e6be;
  --series-b:#228be6; --series-c:#e8590c; }} }}
* {{ box-sizing:border-box; }}
.charts {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(32rem,100%),1fr));
  gap:var(--space); margin-block-end:var(--space); }}
.chart-card {{ margin:0; padding:var(--space); border:1px solid var(--line);
  border-radius:var(--radius); background:var(--surface); min-inline-size:0; }}
figcaption {{ display:flex; justify-content:space-between; align-items:center;
  gap:12px; font-size:14px; font-weight:600; margin-block-end:8px; }}
.legend {{ display:flex; gap:14px; font-weight:400; color:var(--muted); }}
.lg {{ display:inline-flex; align-items:center; gap:6px; }}
.sw {{ inline-size:11px; block-size:11px; border-radius:3px; background:var(--c); }}
.chart {{ inline-size:100%; block-size:auto; }}
.s-b {{ --c:var(--series-b); }}
.s-c {{ --c:var(--series-c); }}
.line {{ fill:none; stroke:var(--c); stroke-width:2; stroke-linejoin:round; stroke-linecap:round; }}
.dot {{ fill:var(--c); }}
.hit {{ fill:transparent; }}
.grid {{ stroke:var(--line); stroke-width:1; }}
.axis {{ fill:var(--muted); font-size:10px; }}
.endlab {{ fill:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }}
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
<div class="charts">{charts_html}</div>
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
