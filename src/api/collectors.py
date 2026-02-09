"""
Data collectors for DGX Spark + OpenClaw monitoring.

Pulls live data from the OpenClaw gateway's infrastructure RPC and health
endpoints instead of doing direct SSH/systemd checks.  The gateway already
collects provider health, tunnel status, and GPU metrics — we just reshape
the output for the dashboard.

Data sources:
  - gateway "infrastructure" RPC → GPU, provider (llama.cpp), tunnel
  - gateway "health" RPC → gateway status, Discord bot status, sessions
"""

import json
import subprocess
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPENCLAW_DIR = "/home/mferr/openclaw"
OPENCLAW_CLI = f"{OPENCLAW_DIR}/dist/entry.js"
RPC_TIMEOUT = 15  # seconds for subprocess calls

# ---------------------------------------------------------------------------
# Gateway RPC helper
# ---------------------------------------------------------------------------

def _gateway_call(method: str, probe: bool = False) -> dict | None:
    """Call a gateway RPC method via the CLI and return parsed JSON."""
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

        # The CLI outputs a header line "Gateway call: <method>" then JSON
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


# ---------------------------------------------------------------------------
# Individual reshaping functions (take pre-fetched RPC data)
# ---------------------------------------------------------------------------

def _reshape_gpu(infra: dict) -> dict:
    """Reshape infrastructure GPU data for the dashboard."""
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

    # Memory fields may be None on unified-memory systems (DGX Spark)
    if mem_used is not None and mem_total is not None:
        result["memory_used_mb"] = mem_used
        result["memory_total_mb"] = mem_total
        result["memory_pct"] = mem_pct or (round(mem_used / mem_total * 100, 1) if mem_total else 0)
    else:
        result["memory_note"] = "Unified memory (shared CPU/GPU)"

    return result


def _reshape_provider(infra: dict) -> dict:
    """Reshape infrastructure provider health data for the dashboard."""
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
        "server": "llama.cpp",
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
    """Reshape infrastructure tunnel data for the dashboard."""
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
    """Reshape gateway health data for the dashboard."""
    if not health:
        return {"status": "error", "error": "Gateway unreachable"}

    result = {
        "status": "ok" if health.get("ok") else "error",
        "gateway_ok": health.get("ok", False),
        "latency_ms": health.get("durationMs"),
        "sessions": [],
        "discord": None,
    }

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


# ---------------------------------------------------------------------------
# Public API (individual collectors for per-endpoint use)
# ---------------------------------------------------------------------------

def collect_gpu_stats() -> dict:
    return _reshape_gpu(_gateway_call("infrastructure"))

def collect_provider_status() -> dict:
    return _reshape_provider(_gateway_call("infrastructure"))

def collect_gateway_status() -> dict:
    return _reshape_gateway(_gateway_call("health"))

def collect_tunnel_status() -> dict:
    return _reshape_tunnel(_gateway_call("infrastructure"))


def collect_all() -> dict:
    """Fetch all data with just 2 RPC calls and return combined results."""
    t0 = time.time()

    # Only 2 RPC calls total — share the infrastructure result across cards
    infra = _gateway_call("infrastructure")
    health = _gateway_call("health")

    gpu = _reshape_gpu(infra)
    provider = _reshape_provider(infra)
    tunnel = _reshape_tunnel(infra)
    gateway = _reshape_gateway(health)

    statuses = [gpu["status"], provider["status"], gateway["status"], tunnel["status"]]
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
    }
