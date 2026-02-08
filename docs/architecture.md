# Architecture

## Overview

The DGX OpenClaw Dashboard is a lightweight monitoring tool that provides
real-time visibility into the DGX Spark + OpenClaw inference infrastructure.
It runs on the WSL host and reaches out to the various subsystems to collect
status information.

## Infrastructure Layout

```
+-------------------------------+
|  WSL2 (Ubuntu on Windows)     |
|                               |
|  +-------------------------+  |       SSH (port 22)
|  | Dashboard (port 8080)   |----------+
|  | - Python http.server    |  |       |
|  | - API endpoints         |  |       |
|  | - Serves HTML/JS UI     |  |       |
|  +----------+--------------+  |       |
|             |                 |       |
|  +----------v--------------+  |       |
|  | SSH Tunnel Service      |  |       |
|  | dgx-spark-tunnel.service|  |       |
|  | localhost:8001 ---------)--------->+
|  +-------------------------+  |       |
|                               |       |
|  +-------------------------+  |       |
|  | OpenClaw Gateway        |  |       |
|  | openclaw-gateway.service|  |       |
|  | (Agent: BillBot)        |  |       |
|  +----------+--------------+  |       |
|             |                 |       |
|  +----------v--------------+  |       |
|  | openclaw-trtllm-proxy   |  |       |
|  | port 8000               |  |       |
|  | Strips unsupported      |  |       |
|  | params, translates      |  |       |
|  | Responses -> Chat API   |  |       |
|  +-------------------------+  |       |
+-------------------------------+       |
                                        |
+---------------------------------------v--+
|  DGX Spark                                |
|  NVIDIA GB10  |  128 GB LPDDR5x  | ARM64 |
|  Ubuntu 24.04                             |
|                                           |
|  +-------------------------------------+ |
|  | vLLM                                | |
|  | Qwen3-Coder-Next-AWQ-4bit           | |
|  | Listening on port 8000              | |
|  +-------------------------------------+ |
|                                           |
|  nvidia-smi (GPU monitoring)              |
+-------------------------------------------+
```

## Data Flow

### 1. GPU Stats (`GET /api/gpu`)

```
Dashboard --SSH--> DGX Spark --nvidia-smi--> parse CSV output
                   mferry@10.0.0.109
```

The dashboard opens an SSH connection to the DGX Spark and runs
`nvidia-smi --query-gpu=... --format=csv`. The CSV output is parsed
into structured fields: utilization, memory, temperature, and power.

### 2. vLLM Health (`GET /api/vllm`)

```
Dashboard --HTTP--> localhost:8001/health --SSH tunnel--> DGX:8000/health
          --HTTP--> localhost:8001/v1/models              DGX:8000/v1/models
```

The SSH tunnel service forwards `localhost:8001` to the DGX port 8000
where vLLM is listening. The dashboard checks the `/health` endpoint
and optionally queries `/v1/models` for loaded model information.

### 3. OpenClaw Gateway (`GET /api/openclaw`)

```
Dashboard --systemctl--> openclaw-gateway.service status
          --journalctl--> recent log entries
```

Checked locally via `systemctl --user is-active`. If active, the
dashboard also pulls the 5 most recent journal entries for display.

### 4. SSH Tunnel (`GET /api/tunnel`)

```
Dashboard --systemctl--> dgx-spark-tunnel.service status
          --ss--> verify port 8001 is listening
```

Checked locally via systemctl. Additionally verifies that port 8001
is actually listening with `ss -tlnp`, catching the case where the
service is "active" but the tunnel has dropped.

### 5. Combined Overview (`GET /api/overview`)

Runs all four collectors and returns a single JSON payload with an
`overall` status derived from the individual results:

- **ok** -- all systems healthy
- **degraded** -- at least one system is degraded, none in error
- **error** -- at least one system is in error state

## Technology Stack

| Component         | Technology                          |
|-------------------|-------------------------------------|
| Server            | Python `http.server` (stdlib)       |
| HTTP client       | `requests` library                  |
| SSH               | `subprocess` calling `ssh` CLI      |
| Service checks    | `systemctl --user`                  |
| Frontend          | Vanilla HTML/CSS/JS                 |
| Styling           | Dark theme, CSS Grid, responsive    |
| Auto-refresh      | `setInterval` + `fetch()` (10s)     |

## File Structure

```
dgx-openclaw-dashboard/
  src/
    dashboard.py            Main server (port 8080)
    api/
      __init__.py
      collectors.py         Data collection functions
  public/
    index.html              Dashboard web UI
  docs/
    architecture.md         This file
  README.md                 Project overview and setup
```
