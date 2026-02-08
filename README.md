# DGX OpenClaw Dashboard

Web-based monitoring dashboard for DGX Spark + OpenClaw inference infrastructure.
Provides real-time visibility into GPU utilization, vLLM model serving, the
OpenClaw agent gateway, and the SSH tunnel connecting everything together.

## Features

- **GPU Monitoring** -- Live GPU utilization, memory, temperature, and power
  drawn from `nvidia-smi` over SSH to the DGX Spark
- **vLLM Health** -- Checks model server health and loaded models through
  the SSH tunnel
- **OpenClaw Gateway Status** -- Monitors the agent gateway systemd service
  with recent journal entries
- **SSH Tunnel Status** -- Verifies the tunnel service is active and the
  forwarded port is actually listening
- **Combined Overview** -- Single endpoint aggregating all subsystem health
  with an overall status indicator
- **Auto-Refresh** -- Dashboard polls every 10 seconds with color-coded
  status cards (green / yellow / red)
- **Dark Theme** -- Clean terminal-inspired aesthetic, responsive layout

## Infrastructure

| Component     | Details                                        |
|---------------|------------------------------------------------|
| DGX Spark     | NVIDIA GB10, 128 GB unified LPDDR5x, ARM64    |
| OS            | Ubuntu 24.04 (DGX), WSL2 Ubuntu (host)        |
| Model         | Qwen3-Coder-Next-AWQ-4bit via vLLM            |
| Agent         | BillBot, managed by OpenClaw gateway           |
| Proxy         | openclaw-trtllm-proxy on port 8000             |
| SSH Tunnel    | WSL localhost:8001 -> DGX port 8000            |

## Quick Start

```bash
# From the project root
python3 src/dashboard.py

# Or specify a different port
python3 src/dashboard.py --port 9090
```

Then open `http://localhost:8080` in your browser.

## Prerequisites

- Python 3.10+
- `requests` library (`pip install requests`)
- SSH key at `~/.ssh/nvsync.key` with access to `mferry@10.0.0.109`
- systemd user services: `dgx-spark-tunnel.service`, `openclaw-gateway.service`
- SSH tunnel forwarding localhost:8001 to DGX port 8000

## API Endpoints

| Endpoint          | Description                                 |
|-------------------|---------------------------------------------|
| `GET /`           | Dashboard HTML page                         |
| `GET /api/gpu`    | GPU stats (utilization, memory, temp, power)|
| `GET /api/vllm`   | vLLM health and model info                  |
| `GET /api/openclaw`| OpenClaw gateway service status            |
| `GET /api/tunnel` | SSH tunnel service status                   |
| `GET /api/overview`| Combined status of all systems             |

All API endpoints return JSON with at minimum a `"status"` field
(`"ok"`, `"degraded"`, or `"error"`).

## Project Structure

```
dgx-openclaw-dashboard/
  src/
    dashboard.py              Main HTTP server
    api/
      __init__.py
      collectors.py           Data collection functions
  public/
    index.html                Dashboard web UI
  docs/
    architecture.md           Infrastructure and data flow docs
  README.md                   This file
```

## Screenshots

_Dashboard screenshots will be added here._

## Configuration

Key configuration values are at the top of `src/api/collectors.py`:

```python
DGX_HOST = "mferry@10.0.0.109"
SSH_KEY  = "/home/mferr/.ssh/nvsync.key"
VLLM_HEALTH_URL = "http://localhost:8001/health"
VLLM_MODELS_URL = "http://localhost:8001/v1/models"
```

Adjust these if your infrastructure differs.

## License

Internal tooling -- not published.
