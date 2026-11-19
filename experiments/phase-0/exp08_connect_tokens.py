# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.4", "websockets==15.0.1"]
# ///
"""PROTOTYPE, throwaway. Phase 0 experiment 8: connect tokens on a VM sandbox.

Answers, from implementation.md §18:
- Q18: how long a connect token lasts, and whether one sandbox can hold tokens
       for two ports. Also whether a second token for a port breaks the first.
- Q19: what a connect URL answers once its sandbox has ended.
- Q7:  whether an idle WebSocket through a connect URL survives 30 minutes.
- Gap from the grilling round: connect tokens on runtime="vm" with no
  declared ports, and what happens to tokens across a memory restore.

T3 listens on 3773. A plain HTTP server stands in for the daemon on 3774.
Tokens are never printed or saved. The sandbox requests 0.25 cores and 1 GiB
and runs about 1 hour 40 minutes, so it costs about $0.10.
Run: uv run experiments/phase-0/exp08_connect_tokens.py
"""

import asyncio
import json
import time
from urllib.parse import urlencode

import modal
import websockets

from common import TAG, app, http, image, log, save, sh, wait_for_t3

CHECK_EVERY_S = 30 * 60
CHECKS = 3  # at 0, 30, 60 and 90 minutes
START = r"""
mkdir -p /root/p0 /root/p0/www && echo daemon-stand-in > /root/p0/www/index.html
t3 serve --host 0.0.0.0 --port 3773 > /root/p0/t3.log 2>&1 &
python3 -m http.server 3774 --directory /root/p0/www > /root/p0/http.log 2>&1 &
wait
"""

result: dict = {"experiment": "exp08_connect_tokens", "steps": {}}
steps = result["steps"]
live: list[modal.Sandbox] = []


def q(token: str) -> str:
    return "?" + urlencode({"_modal_connect_token": token})


def probe(base: str, token: str | None, path: str) -> int:
    status, _ = http(f"{base}{path}{q(token) if token else ''}", timeout=20)
    return status


def probe_set(tokens: dict) -> dict:
    """tokens maps a name to (base url, token, path). Returns name -> HTTP status."""
    return {name: probe(base, tok, path) for name, (base, tok, path) in tokens.items()}


async def idle_ws(url: str, idle_s: int) -> dict:
    rec: dict = {"opened": False, "idle_s": idle_s}
    start = time.time()
    try:
        async with websockets.connect(url, open_timeout=30, ping_interval=None, max_size=None) as ws:
            rec["opened"] = True
            end = time.time() + idle_s
            frames = 0
            while time.time() < end:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time()))
                    frames += 1
                except asyncio.TimeoutError:
                    break
            rec["frames_received"] = frames
            pong = await ws.ping()
            await asyncio.wait_for(pong, timeout=10)
            rec["pong_after_idle"] = True
    except websockets.exceptions.ConnectionClosed as e:
        rec["closed"] = {"code": e.rcvd.code if e.rcvd else None, "reason": e.rcvd.reason if e.rcvd else None,
                         "after_s": round(time.time() - start, 1)}
    except Exception as e:
        rec["error"] = repr(e)
    return rec


def main() -> None:
    log("creating VM sandbox, no declared ports, mode B request")
    sb = modal.Sandbox.create(
        "sh", "-c", START,
        app=app, image=image, runtime="vm", _experimental_enable_snapshot=True,
        cpu=(0.25, 1), memory=(1024, 2048), timeout=3 * 3600, tags={TAG: "exp08"},
    )
    live.append(sb)

    # Tokens for two ports, then a second token for 3773.
    t3a = sb.create_connect_token(port=3773)
    d = sb.create_connect_token(port=3774)
    steps["urls"] = {"t3_and_daemon_url_differ": t3a.url != d.url}
    base_t3, base_d = t3a.url.rstrip("/"), d.url.rstrip("/")

    ready_s, env = wait_for_t3(base_t3, query=q(t3a.token))
    steps["t3_ready_via_connect_url_s"] = round(ready_s, 1)
    log(f"T3 answered through the connect URL after {ready_s:.1f} s")

    t3b = sb.create_connect_token(port=3773)
    steps["urls"]["second_3773_url_same"] = t3b.url.rstrip("/") == base_t3

    env_path = "/.well-known/t3/environment"
    probes = {
        "t3_token_a": (base_t3, t3a.token, env_path),
        "t3_token_b": (base_t3, t3b.token, env_path),
        "daemon_token": (base_d, d.token, "/"),
    }
    steps["initial"] = {
        **probe_set(probes),
        "t3_no_token": probe(base_t3, None, env_path),
        "daemon_no_token": probe(base_d, None, "/"),
        "daemon_token_on_t3_url": probe(base_t3, d.token, env_path),
        "t3_token_on_daemon_url": probe(base_d, t3a.token, "/"),
    }
    log(f"initial: {steps['initial']}")

    # A T3 session and WebSocket ticket for the idle WebSocket test.
    t3_session = sh(sb, "t3 auth session issue --token-only --ttl 3h 2>/dev/null")
    bearer = {"Authorization": f"Bearer {t3_session}"}
    status, body = http(f"{base_t3}/api/auth/websocket-ticket/rpc{q(t3a.token)}", method="POST", headers=bearer)
    ws_url = None
    if status == 200:
        ticket = json.loads(body)["ticket"]
        ws_url = (base_t3.replace("https://", "wss://", 1) + "/ws?"
                  + urlencode({"wsTicket": ticket, "orchestrationProtocol": "2", "_modal_connect_token": t3a.token}))
    steps["ws_ticket_status"] = status

    t0 = time.time()
    lifetime = []
    for i in range(1, CHECKS + 1):
        if i == 1 and ws_url:
            log("holding a WebSocket idle for 30 minutes")
            steps["ws_idle_30m"] = asyncio.run(idle_ws(ws_url, CHECK_EVERY_S))
            log(f"idle WebSocket: {steps['ws_idle_30m']}")
        wait = t0 + i * CHECK_EVERY_S - time.time()
        if wait > 0:
            log(f"sleeping {wait:.0f} s until check {i}")
            time.sleep(wait)
        row = {"minutes": round((time.time() - t0) / 60), **probe_set(probes)}
        lifetime.append(row)
        log(f"check {i}: {row}")
    steps["lifetime"] = lifetime

    log("memory snapshot, which ends the sandbox")
    snap = sb._experimental_snapshot()
    live.remove(sb)
    time.sleep(5)
    steps["after_snapshot_old_url"] = probe_set(probes)
    log(f"old URLs after the snapshot: {steps['after_snapshot_old_url']}")

    log("memory restore")
    t_restore = time.time()
    sb2 = modal.Sandbox._experimental_from_snapshot(snap)
    live.append(sb2)
    n = sb2.create_connect_token(port=3773)
    base_n = n.url.rstrip("/")
    ready_s, env2 = wait_for_t3(base_n, query=q(n.token))
    steps["restore"] = {
        "t3_ready_s": round(time.time() - t_restore, 1),
        "same_environment": env.get("environmentId") == env2.get("environmentId"),
        "new_url_same_as_old": base_n == base_t3,
        "new_token_new_url": probe(base_n, n.token, env_path),
        "old_token_new_url": probe(base_n, t3a.token, env_path),
        "old_token_old_url": probe(base_t3, t3a.token, env_path),
    }
    log(f"restore: {steps['restore']}")

    log("terminating the restored sandbox")
    sb2.terminate()
    live.remove(sb2)
    ended = []
    for delay in (2, 20):
        time.sleep(delay)
        status, body = http(f"{base_n}{env_path}{q(n.token)}", timeout=15)
        ended.append({"after_s": delay, "status": status, "body": body.replace(n.token, "[REDACTED]")[:160]})
    steps["after_terminate"] = ended
    log(f"after terminate: {ended}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        result["error"] = repr(e)
        log(f"ERROR: {e!r}")
    finally:
        for s in live:
            try:
                s.terminate()
            except Exception:
                pass
        save("exp08", result)
