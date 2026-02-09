# DGX OpenClaw Dashboard

Web-based monitoring dashboard for DGX Spark + OpenClaw inference infrastructure.
Provides real-time visibility into GPU utilization, model server health, the
OpenClaw agent gateway (BillBot), Discord status, and the SSH tunnel — all
pulled from the OpenClaw gateway's built-in infrastructure RPC.

## Features

- **GPU Monitoring** -- Live GPU utilization, temperature, and power draw
  from the DGX Spark (collected by the gateway via SSH + nvidia-smi)
- **Model Server Health** -- llama.cpp provider health check with latency
  and consecutive failure tracking
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

| Component     | Details                                        |
|---------------|------------------------------------------------|
| DGX Spark     | NVIDIA GB10, 128 GB unified LPDDR5x, ARM64    |
| OS            | Ubuntu 24.04 (DGX), WSL2 Ubuntu (host)        |
| Model         | Qwen3-Coder-Next-Q4_K_M GGUF via llama.cpp    |
| Context       | 131K tokens (--ctx-size 131072)                |
| Agent         | BillBot, managed by OpenClaw gateway (fork)    |
| SSH Tunnel    | WSL localhost:8001 -> DGX port 8000            |
| Data Source   | OpenClaw gateway RPC (ws://127.0.0.1:18789)   |

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
- OpenClaw fork running at `/home/mferr/openclaw/` with infrastructure monitoring enabled
- SSH tunnel forwarding localhost:8001 to DGX port 8000

No pip packages required — uses Python stdlib only.

## API Endpoints

| Endpoint           | Source RPC       | Description                          |
|--------------------|------------------|--------------------------------------|
| `GET /`            | --               | Dashboard HTML page                  |
| `GET /api/gpu`     | infrastructure   | GPU stats (util%, temp, power)       |
| `GET /api/provider`| infrastructure   | llama.cpp health, latency, failures  |
| `GET /api/gateway` | health           | Gateway + Discord + sessions         |
| `GET /api/tunnel`  | infrastructure   | TCP reachability, service status     |
| `GET /api/overview`| both             | Combined status of all systems       |

All API endpoints return JSON with at minimum a `"status"` field
(`"ok"`, `"degraded"`, or `"error"`).

## Project Structure

```
dgx-openclaw-dashboard/
  src/
    dashboard.py              Main HTTP server (port 8080)
    api/
      __init__.py
      collectors.py           Data reshaping from gateway RPC
  public/
    index.html                Dashboard web UI (dark theme, responsive)
  docs/
    architecture.md           Infrastructure and data flow docs
  README.md                   This file
```

## Configuration

The dashboard pulls all data from the OpenClaw gateway's RPC. Configuration
is in `src/api/collectors.py`:

```python
OPENCLAW_DIR = "/home/mferr/openclaw"
OPENCLAW_CLI = f"{OPENCLAW_DIR}/dist/entry.js"
RPC_TIMEOUT = 15  # seconds
```

The gateway itself is configured via `~/.openclaw/openclaw.json` with
infrastructure monitoring sections for GPU, tunnels, and provider health.

## License

Internal tooling -- not published.
