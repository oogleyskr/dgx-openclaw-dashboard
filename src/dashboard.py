#!/usr/bin/env python3
"""
DGX OpenClaw Dashboard  --  Monitoring server for DGX Spark + OpenClaw infra.

Serves a web dashboard on port 8080 and exposes JSON API endpoints that
collect live metrics from the DGX Spark (GPU stats via SSH), vLLM (HTTP
health check via SSH tunnel), OpenClaw gateway, and the SSH tunnel service.

Usage:
    python3 src/dashboard.py            # starts on 0.0.0.0:8080
    python3 src/dashboard.py --port 9090

No external dependencies beyond stdlib + requests.
"""

import argparse
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Ensure the project root is on sys.path so we can import collectors
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.collectors import (
    collect_gpu_stats,
    collect_vllm_status,
    collect_openclaw_status,
    collect_tunnel_status,
    collect_all,
)

# ---------------------------------------------------------------------------
# HTML / static file serving
# ---------------------------------------------------------------------------

PUBLIC_DIR = PROJECT_ROOT / "public"


def _read_index() -> bytes:
    """Read the dashboard HTML from public/index.html."""
    index = PUBLIC_DIR / "index.html"
    if index.exists():
        return index.read_bytes()
    return b"<html><body><h1>index.html not found</h1></body></html>"


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    """Handles both the static dashboard page and /api/* JSON endpoints."""

    # Suppress default access log noise (we print our own summary)
    def log_message(self, fmt, *args):
        sys.stderr.write(
            f"[dashboard] {self.address_string()} - {fmt % args}\n"
        )

    # -- routing --

    def do_GET(self):
        path = self.path.split("?")[0]  # strip query params

        routes = {
            "/":            self._serve_index,
            "/index.html":  self._serve_index,
            "/api/gpu":     self._api_gpu,
            "/api/vllm":    self._api_vllm,
            "/api/openclaw": self._api_openclaw,
            "/api/tunnel":  self._api_tunnel,
            "/api/overview": self._api_overview,
        }

        handler = routes.get(path)
        if handler:
            handler()
        else:
            # Try serving static files from public/
            self._serve_static(path)

    # -- static files --

    def _serve_index(self):
        self._respond(200, "text/html", _read_index())

    def _serve_static(self, path: str):
        """Serve files under public/ with basic MIME support."""
        safe = path.lstrip("/")
        target = (PUBLIC_DIR / safe).resolve()

        # Prevent directory traversal
        if not str(target).startswith(str(PUBLIC_DIR)):
            self._respond(403, "text/plain", b"Forbidden")
            return

        if target.is_file():
            mime = _guess_mime(target.suffix)
            self._respond(200, mime, target.read_bytes())
        else:
            self._respond(404, "text/plain", b"Not found")

    # -- API endpoints --

    def _api_gpu(self):
        self._json_response(collect_gpu_stats())

    def _api_vllm(self):
        self._json_response(collect_vllm_status())

    def _api_openclaw(self):
        self._json_response(collect_openclaw_status())

    def _api_tunnel(self):
        self._json_response(collect_tunnel_status())

    def _api_overview(self):
        self._json_response(collect_all())

    # -- helpers --

    def _json_response(self, data: dict, status: int = 200):
        body = json.dumps(data, indent=2).encode()
        self._respond(status, "application/json", body)

    def _respond(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def _guess_mime(suffix: str) -> str:
    return {
        ".html": "text/html",
        ".css":  "text/css",
        ".js":   "application/javascript",
        ".json": "application/json",
        ".png":  "image/png",
        ".svg":  "image/svg+xml",
        ".ico":  "image/x-icon",
    }.get(suffix, "application/octet-stream")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="DGX OpenClaw Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="Listen port")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), DashboardHandler)
    print(f"[dashboard] Serving on http://{args.host}:{args.port}")
    print(f"[dashboard] Public dir: {PUBLIC_DIR}")
    print(f"[dashboard] API endpoints:")
    print(f"  GET /api/gpu       - GPU stats via SSH nvidia-smi")
    print(f"  GET /api/vllm      - vLLM health (localhost:8001)")
    print(f"  GET /api/openclaw  - OpenClaw gateway service status")
    print(f"  GET /api/tunnel    - SSH tunnel service status")
    print(f"  GET /api/overview  - Combined status of all systems")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] Shutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
