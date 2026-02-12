"""
Data collectors for the DGX OpenClaw Dashboard.

Pulls live data from three sources:
  1. OpenClaw gateway RPC (infrastructure + health methods)
     - DGX Spark GPU stats (remote, via SSH + nvidia-smi on the DGX)
     - Model provider health (SGLang on DGX)
     - SSH tunnel status
     - Gateway + Discord bot status
  2. Local nvidia-smi (RTX 3090 GPU on this WSL2 host)
  3. Local HTTP health endpoints (multimodal AI services on ports 8101-8104)

Architecture:
  - The gateway RPC calls use subprocess to invoke the OpenClaw CLI, which
    connects to the gateway's WebSocket RPC at ws://127.0.0.1:18789.
  - Local GPU data comes from running nvidia-smi directly.
  - Multimodal service checks use urllib to hit localhost FastAPI health endpoints.
  - collect_all() is the recommended entry point — it makes all calls once
    and returns a single combined response for the dashboard's /api/overview.
"""

import json
import subprocess
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Path to the OpenClaw fork on this machine (used for gateway RPC calls)
OPENCLAW_DIR = "/home/mferr/openclaw"
OPENCLAW_CLI = f"{OPENCLAW_DIR}/dist/entry.js"
RPC_TIMEOUT = 15  # seconds — timeout for gateway RPC subprocess calls

# Multimodal service definitions: name -> port
# These are the FastAPI services running on the local RTX 3090.
MULTIMODAL_SERVICES = {
    "stt":        8101,   # Speech-to-Text (faster-whisper large-v3)
    "vision":     8102,   # Vision-Language (Qwen2.5-VL-7B-AWQ)
    "tts":        8103,   # Text-to-Speech (Kokoro-82M)
    "imagegen":   8104,   # Image Generation (SDXL-Turbo)
}
MULTIMODAL_HEALTH_TIMEOUT = 2  # seconds — timeout per service health check

# Heartbeat LLM server — small model running locally for OpenClaw heartbeat duties
HEARTBEAT_SERVER_PORT = 8200
HEARTBEAT_SERVER_HOST = "localhost"

# OpenClaw config file — read/write for heartbeat model configuration
OPENCLAW_CONFIG_PATH = "/home/mferr/.openclaw/openclaw.json"


# ===========================================================================
# Gateway RPC helpers (for DGX Spark monitoring)
# ===========================================================================

def _gateway_call(method: str, probe: bool = False) -> dict | None:
    """
    Call a gateway RPC method via the OpenClaw CLI and return parsed JSON.

    The CLI outputs a header line ("Gateway call: <method>") followed by the
    JSON response.  We skip the header and parse only the JSON portion.

    Args:
        method: RPC method name (e.g. "infrastructure", "health")
        probe: If True, passes {"probe":true} as params (triggers live checks)

    Returns:
        Parsed JSON dict, or None on any failure (timeout, bad JSON, non-zero exit).
    """
    cmd = ["node", OPENCLAW_CLI, "gateway", "call", method]
    if probe:
        cmd += ["--params", '{"probe":true}']

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=RPC_TIMEOUT,
            cwd=OPENCLAW_DIR,
        )
        if proc.returncode != 0:
            return None

        # Skip the CLI header line and find where JSON starts
        lines = proc.stdout.strip().split("\n")
        json_start = next(
            (i for i, line in enumerate(lines) if line.strip().startswith("{")),
            None,
        )
        if json_start is None:
            return None

        return json.loads("\n".join(lines[json_start:]))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None


# ===========================================================================
# Reshaping functions — transform raw RPC data into dashboard card format
# ===========================================================================

def _reshape_gpu(infra: dict) -> dict:
    """
    Extract DGX Spark GPU stats from the infrastructure RPC response.

    The gateway runs 'nvidia-smi' on the DGX via SSH and returns structured
    GPU data.  We pull the first GPU and compute a status indicator.

    Status logic:
      - "degraded" if utilization > 95% or temperature > 90C
      - "ok" otherwise
      - "error" if data is missing or malformed
    """
    if not infra or "gpu" not in infra:
        return {"status": "error", "error": "No GPU data from gateway"}

    gpu_data = infra["gpu"]
    if gpu_data.get("error"):
        return {"status": "error", "error": gpu_data["error"]}

    gpus = gpu_data.get("gpus", [])
    if not gpus:
        return {"status": "error", "error": "No GPUs reported"}

    gpu = gpus[0]
    util = gpu.get("utilizationPercent", 0)
    temp = gpu.get("temperatureCelsius", 0)
    mem_used = gpu.get("memoryUsedMB")
    mem_total = gpu.get("memoryTotalMB")
    mem_pct = gpu.get("memoryUtilizationPercent")

    if util > 95 or (temp and temp > 90):
        status = "degraded"
    else:
        status = "ok"

    result = {
        "status": status,
        "gpu_name": gpu.get("name", "Unknown"),
        "index": gpu.get("index", 0),
        "utilization_pct": util,
        "temperature_c": temp,
        "power_w": gpu.get("powerDrawWatts"),
        "power_limit_w": gpu.get("powerLimitWatts"),
        "host": gpu_data.get("host", "unknown"),
        "collected_at": gpu_data.get("collectedAt"),
    }

    # Memory fields may be None on unified-memory systems (DGX Spark has
    # 128 GB unified LPDDR5x shared between CPU and GPU)
    if mem_used is not None and mem_total is not None:
        result["memory_used_mb"] = mem_used
        result["memory_total_mb"] = mem_total
        result["memory_pct"] = mem_pct or (round(mem_used / mem_total * 100, 1) if mem_total else 0)
    else:
        result["memory_note"] = "Unified memory (shared CPU/GPU)"

    return result


def _reshape_provider(infra: dict) -> dict:
    """
    Extract model provider (SGLang) health from the infrastructure RPC.

    The gateway periodically probes the LLM server's /models endpoint.
    We report health status, latency, and failure count.
    """
    if not infra or "providers" not in infra:
        return {"status": "error", "error": "No provider data from gateway"}

    providers = infra["providers"].get("providers", {})
    if not providers:
        return {"status": "error", "error": "No providers configured"}

    provider_id, provider = next(iter(providers.items()))

    healthy = provider.get("healthy", False)
    status = "ok" if healthy else "error"

    result = {
        "status": status,
        "provider": provider_id,
        "server": "sglang",
        "base_url": provider.get("baseUrl", ""),
        "healthy": healthy,
        "latency_ms": provider.get("latencyMs"),
        "consecutive_failures": provider.get("consecutiveFailures", 0),
        "last_checked_at": provider.get("lastCheckedAt"),
        "last_healthy_at": provider.get("lastHealthyAt"),
    }

    if provider.get("error"):
        result["error"] = provider["error"]

    return result


def _reshape_tunnel(infra: dict) -> dict:
    """
    Extract SSH tunnel status from the infrastructure RPC.

    The gateway checks TCP reachability of the tunnel port and queries
    systemd for the tunnel service status.

    Status logic:
      - "ok" if port is reachable
      - "degraded" if service is active but port is unreachable
      - "error" if both are down
    """
    if not infra or "tunnels" not in infra:
        return {"status": "error", "error": "No tunnel data from gateway"}

    tunnels = infra.get("tunnels", [])
    if not tunnels:
        return {"status": "error", "error": "No tunnels configured"}

    t = tunnels[0]
    tunnel = t.get("tunnel", {})
    service = t.get("service", {})

    reachable = tunnel.get("reachable", False)
    svc_active = service.get("active", False) if service else None

    if reachable:
        status = "ok"
    elif svc_active:
        status = "degraded"
    else:
        status = "error"

    result = {
        "status": status,
        "host": tunnel.get("host", "unknown"),
        "port": tunnel.get("port"),
        "reachable": reachable,
        "latency_ms": tunnel.get("latencyMs"),
    }

    if tunnel.get("error"):
        result["error"] = tunnel["error"]

    if service:
        result["service_name"] = service.get("name")
        result["service_active"] = svc_active
        result["service_status"] = service.get("status")

    return result


def _reshape_gateway(health: dict) -> dict:
    """
    Extract gateway + Discord bot status from the health RPC.

    The health RPC returns the gateway's own status, active agent sessions,
    and Discord bot connection info (if configured).
    """
    if not health:
        return {"status": "error", "error": "Gateway unreachable"}

    result = {
        "status": "ok" if health.get("ok") else "error",
        "gateway_ok": health.get("ok", False),
        "latency_ms": health.get("durationMs"),
        "sessions": [],
        "discord": None,
    }

    # Extract session info from the first agent
    agents = health.get("agents", [])
    if agents:
        agent = agents[0]
        sessions = agent.get("sessions", {})
        result["session_count"] = sessions.get("count", 0)
        recent = sessions.get("recent", [])
        result["sessions"] = [
            {
                "key": s["key"].split(":")[-1] if ":" in s.get("key", "") else s.get("key", ""),
                "age_seconds": s.get("age", 0) // 1000,
            }
            for s in recent[:5]
        ]

    # Extract Discord bot info (if the discord channel is configured)
    discord = health.get("channels", {}).get("discord", {})
    if discord:
        probe = discord.get("probe", {})
        bot = probe.get("bot", {})
        result["discord"] = {
            "ok": probe.get("ok", False),
            "bot_name": bot.get("username", "Unknown"),
            "bot_id": bot.get("id"),
            "latency_ms": probe.get("elapsedMs"),
        }

    return result


# ===========================================================================
# Local GPU collector (RTX 3090 on this WSL2 host)
# ===========================================================================

def _collect_local_gpu() -> dict:
    """
    Collect GPU metrics from the local machine via nvidia-smi.

    This monitors the RTX 3090 that runs the multimodal services.
    Unlike the DGX GPU stats (which come via gateway RPC), this runs
    nvidia-smi directly as a subprocess.

    Status logic:
      - "ok" normally
      - "degraded" if utilization > 95% or temperature > 90C
      - "error" if nvidia-smi is unavailable or fails
    """
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,temperature.gpu,"
                "power.draw,power.limit,memory.used,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return {"status": "error", "error": "nvidia-smi failed"}

        # Parse CSV: "NVIDIA GeForce RTX 3090, 2, 40, 32.15, 350.00, 22433, 24576, 1894"
        parts = [p.strip() for p in proc.stdout.strip().split(",")]
        if len(parts) < 8:
            return {"status": "error", "error": "Unexpected nvidia-smi output"}

        name = parts[0]
        util = int(parts[1])
        temp = int(parts[2])
        power_draw = float(parts[3])
        power_limit = float(parts[4])
        mem_used = int(parts[5])
        mem_total = int(parts[6])
        mem_free = int(parts[7])
        mem_pct = round(mem_used / mem_total * 100, 1) if mem_total else 0

        # Determine status
        if util > 95 or temp > 90:
            status = "degraded"
        else:
            status = "ok"

        return {
            "status": status,
            "gpu_name": name,
            "utilization_pct": util,
            "temperature_c": temp,
            "power_w": round(power_draw, 1),
            "power_limit_w": round(power_limit, 1),
            "memory_used_mb": mem_used,
            "memory_total_mb": mem_total,
            "memory_free_mb": mem_free,
            "memory_pct": mem_pct,
        }
    except (subprocess.TimeoutExpired, Exception) as e:
        return {"status": "error", "error": str(e)}


# ===========================================================================
# Multimodal services collector (FastAPI services on ports 8101-8104)
# ===========================================================================

def _collect_multimodal() -> dict:
    """
    Check health of all 4 local multimodal AI services.

    Each service exposes GET /health returning JSON like:
      {"status": "ok", "service": "vision", "model": "Qwen/Qwen2.5-VL-7B-Instruct-AWQ"}

    We hit each endpoint and aggregate the results.

    Status logic:
      - "ok" if all services are up
      - "degraded" if some (but not all) are down
      - "error" if all services are down
    """
    services = {}
    up_count = 0

    for name, port in MULTIMODAL_SERVICES.items():
        url = f"http://localhost:{port}/health"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=MULTIMODAL_HEALTH_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
                svc_status = data.get("status", "unknown")
                services[name] = {
                    "status": "ok" if svc_status == "ok" else "loading" if svc_status == "loading" else "error",
                    "port": port,
                    "model": data.get("model"),
                    "service": data.get("service", name),
                }
                if svc_status == "ok":
                    up_count += 1
        except (urllib.error.URLError, TimeoutError, Exception):
            services[name] = {
                "status": "error",
                "port": port,
                "model": None,
                "service": name,
            }

    total = len(MULTIMODAL_SERVICES)
    if up_count == total:
        overall = "ok"
    elif up_count == 0:
        overall = "error"
    else:
        overall = "degraded"

    return {
        "status": overall,
        "services_up": up_count,
        "services_total": total,
        "services": services,
    }


# ===========================================================================
# Heartbeat LLM server collector + config management
# ===========================================================================

def _collect_heartbeat_server() -> dict:
    """
    Check the heartbeat LLM server (Qwen3-1.7B on CPU, port 8200).

    Probes the llama-server /health endpoint and /v1/models for model info.
    Also checks the systemd service status for the heartbeat server.

    Returns:
        Dict with status, model info, server health, and systemd state.
    """
    result = {
        "status": "error",
        "port": HEARTBEAT_SERVER_PORT,
        "model": None,
        "server_healthy": False,
        "service_active": False,
        "service_status": None,
    }

    # Check llama-server health endpoint
    try:
        url = f"http://{HEARTBEAT_SERVER_HOST}:{HEARTBEAT_SERVER_PORT}/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            result["server_healthy"] = data.get("status") == "ok"
    except Exception:
        pass

    # Get model info from /v1/models
    try:
        url = f"http://{HEARTBEAT_SERVER_HOST}:{HEARTBEAT_SERVER_PORT}/v1/models"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            models = data.get("data", [])
            if models:
                m = models[0]
                result["model"] = m.get("id", "unknown")
                meta = m.get("meta", {})
                result["model_params"] = meta.get("n_params")
                result["context_train"] = meta.get("n_ctx_train")
    except Exception:
        pass

    # Check systemd service status
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", "llama-heartbeat.service"],
            capture_output=True, text=True, timeout=5,
        )
        svc_state = proc.stdout.strip()
        result["service_active"] = svc_state == "active"
        result["service_status"] = svc_state
    except Exception:
        pass

    # Determine overall status
    if result["server_healthy"]:
        result["status"] = "ok"
    elif result["service_active"]:
        result["status"] = "degraded"  # service running but server not healthy yet
    else:
        result["status"] = "error"

    return result


def read_heartbeat_config() -> dict:
    """
    Read the current heartbeat configuration from openclaw.json.

    Returns:
        Dict with heartbeat enabled state, model, interval, and available
        providers that could serve as heartbeat models.
    """
    try:
        with open(OPENCLAW_CONFIG_PATH, "r") as f:
            config = json.load(f)
    except Exception as e:
        return {"error": str(e)}

    # Extract heartbeat config from agents.defaults
    agents = config.get("agents", {}).get("defaults", {})
    hb = agents.get("heartbeat", {})

    # Extract available providers and their models for the picker
    providers = config.get("models", {}).get("providers", {})
    available_models = []
    for prov_id, prov in providers.items():
        for model in prov.get("models", []):
            available_models.append({
                "ref": f"{prov_id}/{model['id']}",
                "name": model.get("name", model["id"]),
                "provider": prov_id,
                "model_id": model["id"],
            })

    return {
        "enabled": bool(hb),  # heartbeat is enabled if the section exists
        "model": hb.get("model", ""),
        "every": hb.get("every", "30m"),
        "prompt": hb.get("prompt", ""),
        "available_models": available_models,
    }


def update_heartbeat_config(enabled: bool, model: str = "", every: str = "30m") -> dict:
    """
    Update the heartbeat configuration in openclaw.json.

    Reads the current config, modifies the heartbeat section under
    agents.defaults, and writes it back. Does NOT restart the gateway —
    that must be done separately.

    Args:
        enabled: Whether heartbeat should be active.
        model: Model ref string (e.g. "heartbeat/qwen3-1.7b-q4").
               Empty string means use the agent's default model.
        every: Heartbeat interval (e.g. "30m", "15m", "1h").

    Returns:
        Dict with success status and the new config values.
    """
    try:
        with open(OPENCLAW_CONFIG_PATH, "r") as f:
            config = json.load(f)

        # Ensure agents.defaults exists
        if "agents" not in config:
            config["agents"] = {}
        if "defaults" not in config["agents"]:
            config["agents"]["defaults"] = {}

        if enabled:
            hb = {"every": every}
            if model:
                hb["model"] = model
            config["agents"]["defaults"]["heartbeat"] = hb
        else:
            # Remove heartbeat section to disable
            config["agents"]["defaults"].pop("heartbeat", None)

        with open(OPENCLAW_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")

        return {
            "success": True,
            "enabled": enabled,
            "model": model,
            "every": every,
            "restart_required": True,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ===========================================================================
# Public API — individual collectors for per-endpoint use
# ===========================================================================

def collect_gpu_stats() -> dict:
    """Collect DGX Spark GPU stats via gateway RPC."""
    return _reshape_gpu(_gateway_call("infrastructure"))

def collect_provider_status() -> dict:
    """Collect SGLang provider health via gateway RPC."""
    return _reshape_provider(_gateway_call("infrastructure"))

def collect_gateway_status() -> dict:
    """Collect gateway + Discord status via gateway RPC."""
    return _reshape_gateway(_gateway_call("health"))

def collect_tunnel_status() -> dict:
    """Collect SSH tunnel status via gateway RPC."""
    return _reshape_tunnel(_gateway_call("infrastructure"))

def collect_local_gpu_stats() -> dict:
    """Collect local RTX 3090 GPU stats via nvidia-smi."""
    return _collect_local_gpu()

def collect_multimodal_status() -> dict:
    """Collect health status of multimodal services."""
    return _collect_multimodal()

def collect_heartbeat_status() -> dict:
    """Collect heartbeat LLM server status + config."""
    server = _collect_heartbeat_server()
    config = read_heartbeat_config()
    return {**server, "config": config}


# ===========================================================================
# Combined collector — single call for the dashboard's /api/overview
# ===========================================================================

def collect_all() -> dict:
    """
    Fetch all monitoring data and return a combined response.

    Makes 2 gateway RPC calls (infrastructure + health) plus 1 local
    nvidia-smi call and 4 HTTP health checks to multimodal services.
    All results are reshaped into dashboard card format.

    The overall status is the worst of all subsystem statuses:
      - "ok" if everything is green
      - "error" if any subsystem has an error
      - "degraded" otherwise
    """
    t0 = time.time()

    # Gateway RPC calls (2 total — shared across multiple cards)
    infra = _gateway_call("infrastructure")
    health = _gateway_call("health")

    # Reshape gateway data into card format
    gpu = _reshape_gpu(infra)
    provider = _reshape_provider(infra)
    tunnel = _reshape_tunnel(infra)
    gateway = _reshape_gateway(health)

    # Local data collection (direct, no gateway)
    local_gpu = _collect_local_gpu()
    multimodal = _collect_multimodal()
    heartbeat = collect_heartbeat_status()

    # Compute overall status from all subsystems
    # Note: heartbeat is excluded from overall — it's auxiliary, not critical
    statuses = [
        gpu["status"],
        provider["status"],
        gateway["status"],
        tunnel["status"],
        local_gpu["status"],
        multimodal["status"],
    ]
    if all(s == "ok" for s in statuses):
        overall = "ok"
    elif any(s == "error" for s in statuses):
        overall = "error"
    else:
        overall = "degraded"

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "overall": overall,
        "collect_time_ms": round((time.time() - t0) * 1000),
        "gpu": gpu,
        "provider": provider,
        "gateway": gateway,
        "tunnel": tunnel,
        "local_gpu": local_gpu,
        "multimodal": multimodal,
        "heartbeat": heartbeat,
    }
