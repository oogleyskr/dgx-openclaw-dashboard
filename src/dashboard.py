#!/usr/bin/env python3
"""
DGX OpenClaw Dashboard — Monitoring server for the full inference stack.

Serves a web dashboard on port 8080 and exposes JSON API endpoints that
pull live metrics from multiple sources:

  1. OpenClaw gateway RPC (via subprocess → WebSocket at ws://127.0.0.1:18789)
     - DGX Spark GPU stats (remote, nvidia-smi via SSH)
     - Model provider health (llama.cpp on DGX)
     - SSH tunnel status
     - Gateway + Discord bot status

  2. Local nvidia-smi (direct subprocess)
     - RTX 3090 GPU: utilization, temperature, power, VRAM

  3. Local HTTP health endpoints (multimodal AI services)
     - 6 FastAPI services on ports 8101-8106 (STT, Vision, TTS, ImageGen,
       Embeddings, DocParse)

Usage:
    python3 src/dashboard.py            # starts on 0.0.0.0:8080
    python3 src/dashboard.py --port 9090

No external dependencies beyond Python stdlib.
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
    collect_provider_status,
    collect_gateway_status,
    collect_tunnel_status,
    collect_local_gpu_stats,
    collect_multimodal_status,
    collect_heartbeat_status,
    read_heartbeat_config,
    update_heartbeat_config,
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
    """
    HTTP request handler for the dashboard.

    Routes:
      GET  /                 → Dashboard HTML page
      GET  /api/gpu          → DGX Spark GPU stats (via gateway RPC)
      GET  /api/provider     → llama.cpp health (via gateway RPC)
      GET  /api/gateway      → Gateway + Discord status (via gateway RPC)
      GET  /api/tunnel       → SSH tunnel status (via gateway RPC)
      GET  /api/local-gpu    → Local RTX 3090 GPU stats (via nvidia-smi)
      GET  /api/multimodal   → Multimodal service health (HTTP)
      GET  /api/heartbeat    → Heartbeat LLM server status + config
      GET  /api/overview     → Combined status of all systems (recommended)
      POST /api/heartbeat    → Update heartbeat config (enable/model/interval)
      <other>               → Static file from public/ directory
    """

    def log_message(self, fmt, *args):
        """Override default logging to use a cleaner format."""
        sys.stderr.write(
            f"[dashboard] {self.address_string()} - {fmt % args}\n"
        )

    def do_GET(self):
        """Route GET requests to the appropriate handler."""
        path = self.path.split("?")[0]

        routes = {
            "/":                self._serve_index,
            "/index.html":      self._serve_index,
            "/api/gpu":         self._api_gpu,
            "/api/provider":    self._api_provider,
            "/api/gateway":     self._api_gateway,
            "/api/tunnel":      self._api_tunnel,
            "/api/local-gpu":   self._api_local_gpu,
            "/api/multimodal":  self._api_multimodal,
            "/api/heartbeat":   self._api_heartbeat,
            "/api/overview":    self._api_overview,
        }

        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._serve_static(path)

    # --- Page handlers ---

    def _serve_index(self):
        """Serve the main dashboard HTML page."""
        self._respond(200, "text/html", _read_index())

    def _serve_static(self, path: str):
        """Serve static files from public/ with path traversal protection."""
        safe = path.lstrip("/")
        target = (PUBLIC_DIR / safe).resolve()
        if not str(target).startswith(str(PUBLIC_DIR)):
            self._respond(403, "text/plain", b"Forbidden")
            return
        if target.is_file():
            mime = _guess_mime(target.suffix)
            self._respond(200, mime, target.read_bytes())
        else:
            self._respond(404, "text/plain", b"Not found")

    # --- API handlers (gateway RPC) ---

    def _api_gpu(self):
        """DGX Spark GPU stats from gateway infrastructure RPC."""
        self._json_response(collect_gpu_stats())

    def _api_provider(self):
        """llama.cpp model provider health from gateway infrastructure RPC."""
        self._json_response(collect_provider_status())

    def _api_gateway(self):
        """Gateway + Discord bot status from gateway health RPC."""
        self._json_response(collect_gateway_status())

    def _api_tunnel(self):
        """SSH tunnel status from gateway infrastructure RPC."""
        self._json_response(collect_tunnel_status())

    # --- API handlers (local) ---

    def _api_local_gpu(self):
        """Local RTX 3090 GPU stats from nvidia-smi."""
        self._json_response(collect_local_gpu_stats())

    def _api_multimodal(self):
        """Health status of multimodal AI services."""
        self._json_response(collect_multimodal_status())

    def _api_heartbeat(self):
        """Heartbeat LLM server status + config (GET) or update config (POST handled in do_POST)."""
        self._json_response(collect_heartbeat_status())

    # --- API handlers (combined) ---

    def _api_overview(self):
        """Combined status of all monitored systems (recommended endpoint)."""
        self._json_response(collect_all())

    # --- POST handlers ---

    def do_POST(self):
        """Route POST requests for config updates."""
        path = self.path.split("?")[0]

        if path == "/api/heartbeat":
            self._post_heartbeat()
        else:
            self._respond(404, "text/plain", b"Not found")

    def _post_heartbeat(self):
        """
        Update heartbeat config in openclaw.json.

        Expected JSON body:
            {"enabled": true, "model": "heartbeat/qwen3-1.7b-q4", "every": "30m"}

        Note: Changes take effect after gateway restart.
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())

            enabled = body.get("enabled", True)
            model = body.get("model", "")
            every = body.get("every", "30m")

            result = update_heartbeat_config(enabled, model, every)
            status = 200 if result.get("success") else 500
            self._json_response(result, status)
        except (json.JSONDecodeError, Exception) as e:
            self._json_response({"success": False, "error": str(e)}, 400)

    # --- Response helpers ---

    def _json_response(self, data: dict, status: int = 200):
        """Send a JSON response with CORS headers."""
        body = json.dumps(data, indent=2).encode()
        self._respond(status, "application/json", body)

    def _respond(self, status: int, content_type: str, body: bytes):
        """Send an HTTP response with standard headers."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def _guess_mime(suffix: str) -> str:
    """Map file extension to MIME type for static file serving."""
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
    """Start the dashboard HTTP server."""
    parser = argparse.ArgumentParser(description="DGX OpenClaw Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="Listen port")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), DashboardHandler)
    print(f"[dashboard] Serving on http://{args.host}:{args.port}")
    print(f"[dashboard] Public dir: {PUBLIC_DIR}")
    print(f"[dashboard] Data sources:")
    print(f"  - OpenClaw gateway RPC (ws://127.0.0.1:18789)")
    print(f"  - Local nvidia-smi (RTX 3090)")
    print(f"  - Multimodal services (localhost:8101-8104)")
    print(f"  - Heartbeat LLM server (localhost:8200)")
    print(f"[dashboard] API endpoints:")
    print(f"  GET  /api/gpu         - DGX Spark GPU (via gateway RPC)")
    print(f"  GET  /api/provider    - llama.cpp health (via gateway RPC)")
    print(f"  GET  /api/gateway     - Gateway + Discord (via gateway RPC)")
    print(f"  GET  /api/tunnel      - SSH tunnel (via gateway RPC)")
    print(f"  GET  /api/local-gpu   - RTX 3090 GPU (local nvidia-smi)")
    print(f"  GET  /api/multimodal  - Multimodal services (local HTTP)")
    print(f"  GET  /api/heartbeat   - Heartbeat LLM server + config")
    print(f"  POST /api/heartbeat   - Update heartbeat config")
    print(f"  GET  /api/overview    - Combined status (all sources)")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] Shutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
