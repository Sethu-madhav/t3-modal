"""PROTOTYPE, throwaway. Shared helpers for the Phase 0 experiments.

Kept as the record of Phase 0. See experiments/phase-0/README.md.
"""

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import modal

APP_NAME = "t3modal-phase0"
TAG = "t3modal-phase0"
RESULTS = Path(__file__).parent / "results"

# T3 starts headless on port 3773. A ticker writes one line a second and keeps
# its PID, so a memory restore can be told apart from a fresh boot.
START = r"""
mkdir -p /root/p0
t3 serve --host 0.0.0.0 --port 3773 > /root/p0/t3.log 2>&1 &
echo $! > /root/p0/t3.pid
(while true; do date +%s >> /root/p0/ticks; sleep 1; done) &
echo $! > /root/p0/ticker.pid
wait
"""

app = modal.App.lookup(APP_NAME, create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "ca-certificates", "git", "sqlite3", "procps")
    .run_commands(
        "curl -fsSL https://t3.codes/install.sh | sh",
        "/root/.local/bin/t3 --version",
    )
    .env({"PATH": "/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"})
)

_T0 = time.time()
# Connect tokens and WebSocket tickets travel in URLs, and an exception message
# can quote the URL. Everything logged or saved goes through redact().
_SECRET = re.compile(r"(_modal_connect_token=|wsTicket=)[^&\s'\"\\]+|sk-ant-[A-Za-z0-9_-]+")


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: (m.group(1) or "") + "[REDACTED]", text)


def log(msg: str) -> None:
    print(redact(f"[{time.time() - _T0:7.1f}s] {msg}"), flush=True)


def http(url: str, *, method: str = "GET", headers: dict | None = None, timeout: float = 15):
    """Return (status, body text). Status 0 means no HTTP answer at all."""
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # DNS, TLS, timeout, reset
        return 0, f"{type(e).__name__}: {e}"


def wait_for_t3(base_url: str, limit: float = 240, query: str = "") -> tuple[float, dict]:
    """Poll T3's public environment endpoint. Return (seconds waited, descriptor)."""
    start = time.time()
    last = None
    while time.time() - start < limit:
        status, body = http(f"{base_url}/.well-known/t3/environment{query}", timeout=10)
        if status == 200:
            return time.time() - start, json.loads(body)
        last = (status, body[:200])
        time.sleep(2)
    raise TimeoutError(f"T3 did not answer within {limit}s, last answer {last}")


def sh(sb: modal.Sandbox, cmd: str) -> str:
    p = sb.exec("sh", "-c", cmd)
    p.wait()
    return p.stdout.read().strip()


def save(name: str, result: dict) -> Path:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(redact(json.dumps(result, indent=2, default=str)))
    log(f"results written to {path}")
    return path
