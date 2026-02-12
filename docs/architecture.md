# Architecture

## Overview

The DGX OpenClaw Dashboard is a lightweight monitoring tool that provides
real-time visibility into the DGX Spark + OpenClaw inference infrastructure.
It runs on the WSL host and pulls all monitoring data from the OpenClaw
gateway's built-in infrastructure RPC — no direct SSH or systemd calls.

## Infrastructure Layout

```
+-------------------------------+
|  WSL2 (Ubuntu on Windows)     |
|                               |
|  +-------------------------+  |
|  | Dashboard (port 8080)   |  |
|  | - Python http.server    |  |
|  | - API endpoints         |  |
|  | - Serves HTML/JS UI     |  |
|  +----------+--------------+  |
|             | RPC calls       |
|  +----------v--------------+  |       SSH (port 22)
|  | OpenClaw Gateway        |----------+
|  | ws://127.0.0.1:18789    |  |       |
|  | (Agent: BillBot)        |  |       |
|  |                         |  |       |
|  | Built-in monitors:      |  |       |
|  |  - Provider health      |  |       |
|  |  - Tunnel monitor       |  |       |
|  |  - GPU metrics (SSH)  --------+    |
|  +----------+--------------+  |  |    |
|             |                 |  |    |
|  +----------v--------------+  |  |    |
|  | SSH Tunnel Service      |  |  |    |
|  | dgx-spark-tunnel.service|  |  |    |
|  | localhost:8001 ---------)--+--+--->+
|  +-------------------------+  |       |
+-------------------------------+       |
                                        |
+---------------------------------------v--+
|  DGX Spark                                |
|  NVIDIA GB10  |  128 GB LPDDR5x  | ARM64 |
|  Ubuntu 24.04                             |
|                                           |
|  +-------------------------------------+ |
|  | SGLang (Docker)                     | |
|  | gpt-oss-120b MXFP4                  | |
|  | context 131072                      | |
|  | Listening on port 8000              | |
|  +-------------------------------------+ |
|                                           |
|  nvidia-smi (GPU monitoring)              |
+-------------------------------------------+
```

## Data Flow

The dashboard makes **2 RPC calls** to the OpenClaw gateway per poll cycle:

### 1. Infrastructure RPC (`gateway call infrastructure`)

Returns GPU, provider health, and tunnel status in a single response.

```
Dashboard --subprocess--> node dist/entry.js gateway call infrastructure
                              |
                              +-- providers: health check results (HTTP probe)
                              +-- tunnels: TCP reachability + systemd status
                              +-- gpu: nvidia-smi over SSH to DGX
```

### 2. Health RPC (`gateway call health`)

Returns gateway status, Discord connection, and session info.

```
Dashboard --subprocess--> node dist/entry.js gateway call health
                              |
                              +-- ok: boolean gateway health
                              +-- channels.discord: bot connection + probe
                              +-- agents[].sessions: active session list
```

### Dashboard API Endpoints

| Endpoint          | Source RPC       | Data                                   |
|-------------------|------------------|----------------------------------------|
| `GET /api/gpu`    | infrastructure   | GPU name, util%, temp, power           |
| `GET /api/provider`| infrastructure  | SGLang health, latency, failures       |
| `GET /api/gateway`| health           | Gateway status, Discord, sessions      |
| `GET /api/tunnel` | infrastructure   | TCP reachability, service status       |
| `GET /api/overview`| both            | All of the above, combined             |

### Overall Status Logic

- **ok** -- all systems healthy
- **degraded** -- at least one system is degraded, none in error
- **error** -- at least one system is in error state

## Technology Stack

| Component         | Technology                              |
|-------------------|-----------------------------------------|
| Server            | Python `http.server` (stdlib)           |
| Data source       | OpenClaw gateway RPC (via CLI subprocess)|
| Frontend          | Vanilla HTML/CSS/JS                     |
| Styling           | Dark theme, CSS Grid, responsive        |
| Auto-refresh      | `setInterval` + `fetch()` (10s)         |
| Dependencies      | Python stdlib only (no pip packages)    |

## File Structure

```
dgx-openclaw-dashboard/
  src/
    dashboard.py            Main server (port 8080)
    api/
      __init__.py
      collectors.py         Data reshaping from gateway RPC
  public/
    index.html              Dashboard web UI
  docs/
    architecture.md         This file
  README.md                 Project overview and setup
```
