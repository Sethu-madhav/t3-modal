# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.4", "websockets==15.0.1"]
# ///
"""PROTOTYPE, throwaway. Phase 0 experiment 2: T3 through a connect-token URL.

Answers, from implementation.md §18:
- Q16: does a connect token protect T3's port, and do T3's HTTP calls and its
       WebSocket work through the connect-token URL?
- Q7 in part: does an idle WebSocket stay open for 75 s through each route?
- Is a plain encrypted_ports tunnel reachable with no credentials?

Tokens are never printed. Cost is about $0.03.
Run: uv run experiments/phase-0/exp02_connect_token.py
"""

import asyncio
import json
import time
from urllib.parse import urlencode

import modal
import websockets

from common import START, TAG, app, http, image, log, save, sh, wait_for_t3

result: dict = {"experiment": "exp02_connect_token", "checks": {}}
checks = result["checks"]
IDLE_S = 75


def ws_url(base: str, ticket: str, extra: dict | None = None) -> str:
    # T3 answers 426 unless the client names the orchestration protocol it speaks.
    q = {"wsTicket": ticket, "orchestrationProtocol": "2", **(extra or {})}
    return base.replace("https://", "wss://", 1) + "/ws?" + urlencode(q)


async def hold_ws(name: str, url: str) -> None:
    rec: dict = {"opened": False}
    checks[name] = rec
    try:
        async with websockets.connect(url, open_timeout=30, ping_interval=None, max_size=None) as ws:
            rec["opened"] = True
            frames = 0
            end = time.time() + IDLE_S
            while time.time() < end:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time()))
                    frames += 1
                except asyncio.TimeoutError:
                    break
            rec["frames_received"] = frames
            pong = await ws.ping()
            await asyncio.wait_for(pong, timeout=10)
            rec["open_after_idle_s"] = IDLE_S
    except websockets.exceptions.InvalidStatus as e:
        rec["http_status"] = e.response.status_code
    except websockets.exceptions.ConnectionClosed as e:
        rec["closed"] = {"code": e.rcvd.code if e.rcvd else None, "reason": e.rcvd.reason if e.rcvd else None}
    except Exception as e:
        rec["error"] = repr(e)
    log(f"{name}: {rec}")


def ticket(base: str, headers: dict, query: str = "") -> str | None:
    status, body = http(f"{base}/api/auth/websocket-ticket/rpc{query}", method="POST", headers=headers)
    if status != 200:
        log(f"ticket request failed with {status}: {body[:200]}")
        return None
    return json.loads(body)["ticket"]


def main() -> None:
    sb = modal.Sandbox.create("sh", "-c", START, app=app, image=image, cpu=1, memory=2048, timeout=600,
                              encrypted_ports=[3773], tags={TAG: "exp02"})
    try:
        tunnel = sb.tunnels()[3773].url
        wait_for_t3(tunnel)
        log("T3 is up")

        t3_token = sh(sb, "t3 auth session issue --token-only --ttl 1h 2>/dev/null")
        checks["t3_token_issued"] = bool(t3_token) and " " not in t3_token
        bearer = {"Authorization": f"Bearer {t3_token}"}

        creds = sb.create_connect_token(port=3773)
        base = creds.url.rstrip("/")
        mq = "?" + urlencode({"_modal_connect_token": creds.token})
        result["connect_url_host"] = base.split("/")[2]

        def rec(name, status_body):
            status, body = status_body
            checks[name] = {"status": status, "body": body[:200]}
            log(f"{name}: {status}")

        rec("tunnel_no_credentials_environment", http(f"{tunnel}/.well-known/t3/environment"))
        rec("connect_no_token_environment", http(f"{base}/.well-known/t3/environment"))
        rec("connect_query_token_environment", http(f"{base}/.well-known/t3/environment{mq}"))
        rec("connect_header_token_environment",
            http(f"{base}/.well-known/t3/environment", headers={"Authorization": f"Bearer {creds.token}"}))
        rec("connect_query_token_t3_session", http(f"{base}/api/auth/session{mq}", headers=bearer))
        rec("tunnel_t3_session", http(f"{tunnel}/api/auth/session", headers=bearer))
        # The collision: Modal's token in Authorization leaves no room for T3's.
        rec("connect_header_token_t3_session_without_t3_bearer",
            http(f"{base}/api/auth/session", headers={"Authorization": f"Bearer {creds.token}"}))

        t_connect = ticket(base, bearer, mq)
        t_tunnel = ticket(tunnel, bearer)
        checks["connect_ticket_issued"] = t_connect is not None
        checks["tunnel_ticket_issued"] = t_tunnel is not None

        jobs = []
        if t_connect:
            jobs.append(hold_ws("ws_connect_url", ws_url(base, t_connect, {"_modal_connect_token": creds.token})))
        if t_tunnel:
            jobs.append(hold_ws("ws_tunnel", ws_url(tunnel, t_tunnel)))
        jobs.append(hold_ws("ws_connect_url_no_modal_token", ws_url(base, "invalid")))
        log(f"holding WebSockets idle for {IDLE_S} s")

        async def run():
            await asyncio.gather(*jobs)

        asyncio.run(run())
    finally:
        sb.terminate()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        result["error"] = repr(e)
        log(f"ERROR: {e!r}")
    finally:
        save("exp02", result)
