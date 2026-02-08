"""
Data collectors for DGX Spark + OpenClaw monitoring.

Each collector gathers metrics from a specific subsystem and returns
a dict with at minimum a "status" field ("ok", "error", or "degraded").
"""

import subprocess
import time
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DGX_HOST = "mferry@10.0.0.109"
SSH_KEY = "/home/mferr/.ssh/nvsync.key"
SSH_OPTS = [
    "-i", SSH_KEY,
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=no",
    "-o", "BatchMode=yes",
]
VLLM_HEALTH_URL = "http://localhost:8001/health"
VLLM_MODELS_URL = "http://localhost:8001/v1/models"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ssh_run(cmd: str, timeout: int = 10) -> tuple[int, str, str]:
    """Run a command on the DGX over SSH.  Returns (returncode, stdout, stderr)."""
    full_cmd = ["ssh"] + SSH_OPTS + [DGX_HOST, cmd]
    try:
        proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "SSH command timed out"
    except Exception as exc:
        return -1, "", str(exc)


def _systemctl_active(service: str) -> bool:
    """Check if a user-level systemd service is active locally."""
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", service],
            capture_output=True, text=True, timeout=5,
        )
        return proc.stdout.strip() == "active"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def collect_gpu_stats() -> dict:
    """SSH to DGX and parse nvidia-smi output.

    Returns GPU utilization, memory usage, temperature, and power draw.
    """
    query = "utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,name,driver_version"
    rc, stdout, stderr = _ssh_run(
        f"nvidia-smi --query-gpu={query} --format=csv,noheader,nounits"
    )

    if rc != 0:
        return {
            "status": "error",
            "error": stderr.strip() or "nvidia-smi failed",
        }

    try:
        parts = [p.strip() for p in stdout.strip().split(",")]
        gpu_util = int(parts[0])
        mem_used = int(parts[1])
        mem_total = int(parts[2])
        temp = int(parts[3])
        power = float(parts[4])
        name = parts[5]
        driver = parts[6]

        # Determine health colour
        if gpu_util > 95 or temp > 90:
            status = "degraded"
        else:
            status = "ok"

        return {
            "status": status,
            "gpu_name": name,
            "driver_version": driver,
            "utilization_pct": gpu_util,
            "memory_used_mb": mem_used,
            "memory_total_mb": mem_total,
            "memory_pct": round(mem_used / mem_total * 100, 1) if mem_total else 0,
            "temperature_c": temp,
            "power_w": power,
        }
    except Exception as exc:
        return {"status": "error", "error": f"Parse error: {exc}", "raw": stdout}


def collect_vllm_status() -> dict:
    """Check vLLM health through the SSH tunnel (localhost:8001)."""
    result: dict = {"status": "error"}
    try:
        resp = requests.get(VLLM_HEALTH_URL, timeout=5)
        if resp.status_code == 200:
            result["status"] = "ok"
            result["health"] = "healthy"
        else:
            result["health"] = f"HTTP {resp.status_code}"
    except requests.ConnectionError:
        result["error"] = "Connection refused (tunnel down?)"
        return result
    except requests.Timeout:
        result["error"] = "Health check timed out"
        return result

    # Also grab model info if healthy
    try:
        resp = requests.get(VLLM_MODELS_URL, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("data", [])
            if models:
                result["model"] = models[0].get("id", "unknown")
                result["model_count"] = len(models)
    except Exception:
        pass  # model info is optional

    return result


def collect_openclaw_status() -> dict:
    """Check if the OpenClaw gateway systemd service is running."""
    active = _systemctl_active("openclaw-gateway.service")
    result = {
        "status": "ok" if active else "error",
        "service": "openclaw-gateway.service",
        "state": "active" if active else "inactive",
    }

    # Try to get recent log entries
    if active:
        try:
            proc = subprocess.run(
                ["journalctl", "--user", "-u", "openclaw-gateway.service",
                 "-n", "5", "--no-pager", "-o", "short-iso"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0:
                result["recent_logs"] = proc.stdout.strip().split("\n")
        except Exception:
            pass

    return result


def collect_tunnel_status() -> dict:
    """Check if the SSH tunnel systemd service is running."""
    active = _systemctl_active("dgx-spark-tunnel.service")
    result = {
        "status": "ok" if active else "error",
        "service": "dgx-spark-tunnel.service",
        "state": "active" if active else "inactive",
    }

    # Verify tunnel is actually passing traffic by checking port
    if active:
        try:
            proc = subprocess.run(
                ["ss", "-tlnp"],
                capture_output=True, text=True, timeout=5,
            )
            if "8001" in proc.stdout:
                result["port_8001"] = "listening"
            else:
                result["port_8001"] = "not listening"
                result["status"] = "degraded"
        except Exception:
            pass

    return result


def collect_all() -> dict:
    """Run every collector and return combined results with a timestamp."""
    t0 = time.time()

    gpu = collect_gpu_stats()
    vllm = collect_vllm_status()
    openclaw = collect_openclaw_status()
    tunnel = collect_tunnel_status()

    statuses = [gpu["status"], vllm["status"], openclaw["status"], tunnel["status"]]
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
        "vllm": vllm,
        "openclaw": openclaw,
        "tunnel": tunnel,
    }
