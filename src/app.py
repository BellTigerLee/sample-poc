"""sample-poc identity beacon.

Tiny stdlib-only HTTP server that reports which pod/node it is running on, so a
Karmada placement onto a specific member cluster can be verified by eye.
"""

import json
import os
import socket
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8080


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, content_type: str = "application/json") -> None:
        payload = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path == "/healthz":
            self._send(200, "ok\n", "text/plain")
            return
        beacon = {
            "app": "sample-poc",
            "pod": os.environ.get("POD_NAME", socket.gethostname()),
            "node": os.environ.get("NODE_NAME", "unknown"),
            "time": datetime.now(timezone.utc).isoformat(),
        }
        self._send(200, json.dumps(beacon) + "\n")

    def log_message(self, *args) -> None:  # silence per-request stderr logging
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
