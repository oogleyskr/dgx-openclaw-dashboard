# DGX OpenClaw Dashboard

Web-based monitoring dashboard for the full inference stack: DGX Spark (remote
LLM), RTX 3090 (local multimodal AI services), OpenClaw gateway, and supporting
infrastructure. Provides real-time visibility into GPU utilization, model server
health, 6 multimodal AI services, Discord bot status, and SSH tunnel — all
from a single dark-themed web UI.

## Features

- **DGX Spark GPU Monitoring** -- Remote GPU utilization, temperature, and power
  draw from the DGX Spark (collected by the gateway via SSH + nvidia-smi)
- **RTX 3090 GPU Monitoring** -- Local VRAM usage, utilization, temperature,
  and power draw (direct nvidia-smi, critical for tracking multimodal service memory)
- **Model Server Health** -- llama.cpp provider health check with latency
  and consecutive failure tracking
- **Multimodal Services** -- Health status of 6 local AI services (STT, Vision,
  TTS, ImageGen, Embeddings, DocParse) with model names and port numbers
- **OpenClaw Gateway Status** -- Gateway health, Discord bot connection,
  active session list with ages
- **SSH Tunnel Status** -- TCP reachability test and systemd service status
- **Combined Overview** -- Single endpoint aggregating all subsystem health
  with an overall status indicator
- **Auto-Refresh** -- Dashboard polls every 10 seconds with color-coded
  status cards (green / yellow / red)
- **Dark Theme** -- Clean terminal-inspired aesthetic, responsive layout
- **Zero External Dependencies** -- Python stdlib only, no pip packages
- **Tailscale Ready** -- Access from anywhere via Tailscale mesh VPN

## Infrastructure

| Component         | Details                                          |
|-------------------|--------------------------------------------------|
| DGX Spark         | NVIDIA GB10, 128 GB unified LPDDR5x, ARM64      |
| RTX 3090          | 24 GB GDDR6X, runs 6 multimodal AI services     |
| OS                | Ubuntu 24.04 (DGX), WSL2 Ubuntu (host)          |
| LLM               | Qwen3-Coder-Next-Q4_K_M GGUF via llama.cpp      |
| Multimodal        | STT, Vision, TTS, ImageGen, Embeddings, DocParse |
| Agent             | BillBot, managed by OpenClaw gateway (fork)      |
| SSH Tunnel        | WSL localhost:8001 -> DGX port 8000              |
| Data Sources      | Gateway RPC + local nvidia-smi + HTTP health     |

## Quick Start

```bash
# From the project root
python3 src/dashboard.py

# Or specify a different port
python3 src/dashboard.py --port 9090
```

Then open `http://localhost:8080` in your browser.

### Remote Access via Tailscale

If Tailscale is set up on both the host PC and your device:

```bash
# Get the host's Tailscale IP
powershell.exe -Command "tailscale ip -4"

# Set up WSL2 port forward (one-time)
WSL_IP=$(hostname -I | awk '{print $1}')
powershell.exe -Command "netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 connectport=8080 connectaddress=$WSL_IP"

# Add firewall rule (one-time)
powershell.exe -Command "New-NetFirewallRule -DisplayName 'DGX Dashboard' -Direction Inbound -LocalPort 8080 -Protocol TCP -Action Allow"
```

Then access from any Tailscale device: `http://<tailscale-ip>:8080`

## Prerequisites

- Python 3.10+
- OpenClaw fork running at `/home/mferr/openclaw/` with infrastructure monitoring
- SSH tunnel forwarding localhost:8001 to DGX port 8000
- Multimodal services running on ports 8101-8106 (optional — shows DOWN if not running)

No pip packages required — uses Python stdlib only.

## API Endpoints

| Endpoint             | Source                | Description                          |
|----------------------|-----------------------|--------------------------------------|
| `GET /`              | --                    | Dashboard HTML page                  |
| `GET /api/gpu`       | Gateway RPC           | DGX Spark GPU stats                  |
| `GET /api/provider`  | Gateway RPC           | llama.cpp health, latency, failures  |
| `GET /api/gateway`   | Gateway RPC           | Gateway + Discord + sessions         |
| `GET /api/tunnel`    | Gateway RPC           | TCP reachability, service status     |
| `GET /api/local-gpu` | Local nvidia-smi      | RTX 3090 GPU, VRAM, temp, power      |
| `GET /api/multimodal`| Local HTTP            | 6 multimodal services health         |
| `GET /api/overview`  | All sources           | Combined status of all systems       |

All API endpoints return JSON with at minimum a `"status"` field
(`"ok"`, `"degraded"`, or `"error"`).

## Project Structure

```
dgx-openclaw-dashboard/
  src/
    dashboard.py              Main HTTP server (port 8080)
    api/
      __init__.py
      collectors.py           Data collection from 3 sources:
                                - Gateway RPC (DGX GPU, provider, tunnel)
                                - Local nvidia-smi (RTX 3090)
                                - Local HTTP (multimodal services)
  public/
    index.html                Dashboard web UI (dark theme, 6 cards + log)
  docs/
    architecture.md           Infrastructure and data flow docs
  README.md                   This file
```

## Configuration

Data collection is configured in `src/api/collectors.py`:

```python
# Gateway RPC (DGX Spark monitoring)
OPENCLAW_DIR = "/home/mferr/openclaw"
OPENCLAW_CLI = f"{OPENCLAW_DIR}/dist/entry.js"
RPC_TIMEOUT = 15  # seconds

# Multimodal services (local HTTP health checks)
MULTIMODAL_SERVICES = {
    "stt": 8101, "vision": 8102, "tts": 8103,
    "imagegen": 8104, "embeddings": 8105, "docutils": 8106,
}
MULTIMODAL_HEALTH_TIMEOUT = 2  # seconds per service
```

The gateway itself is configured via `~/.openclaw/openclaw.json` with
infrastructure monitoring sections for GPU, tunnels, and provider health.

## License

Internal tooling -- not published.
