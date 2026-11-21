"""Real-Modal test for #7, #8 and #9: create a machine through the deployed controller, reach its T3,
send a signed heartbeat with its machine secret, and check the daemon, its heartbeats and Docker.

Opt-in: T3MODAL_IT=1 uv run pytest tests/it -s
It deploys the controller as t3modal-controller-it, with its own Volume, Dict and admin Secret, and
creates one small machine. The first run builds the machine image, which takes several minutes.
Teardown stops the app, ends every sandbox in it, and checks that 0 are left.
Connect tokens, the admin token, the HMAC key and the machine secret are never printed.
"""

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("T3MODAL_IT") != "1", reason="real-Modal suite, set T3MODAL_IT=1")

CONTROLLER_DIR = Path(__file__).resolve().parents[2]
APP_NAME = "t3modal-controller-it"
T3_VERSION = os.environ.get("T3MODAL_IT_T3_VERSION", "0.1.10")


def http(
    method: str, url: str, *, token: str | None = None, body: dict | bytes | None = None, headers: dict | None = None, timeout: float = 60
):
    """Returns (status, parsed JSON or text)."""
    headers = {"Content-Type": "application/json", **(headers or {})}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = body if body is None or isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode("utf-8", "replace")
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def log(msg: str) -> None:
    print(f"[it] {msg}", flush=True)


@pytest.fixture(scope="module")
def controller():
    import modal

    admin = secrets.token_urlsafe(32)
    modal.Secret.objects.delete("t3modal-admin-it", allow_missing=True)
    modal.Secret.objects.create("t3modal-admin-it", {"ADMIN_TOKEN": admin})
    modal.Secret.objects.delete("t3modal-hmac-it", allow_missing=True)
    modal.Secret.objects.create("t3modal-hmac-it", {"HMAC_KEY": secrets.token_urlsafe(32)})

    log("deploying t3modal-controller-it")
    env = {**os.environ, "T3MODAL_NAME_SUFFIX": "-it"}
    deploy = subprocess.run(
        [sys.executable, "-m", "modal", "deploy", "app.py"], cwd=CONTROLLER_DIR, env=env, capture_output=True, text=True
    )
    assert deploy.returncode == 0, deploy.stdout[-3000:] + deploy.stderr[-3000:]
    url = modal.Function.from_name(APP_NAME, "api").get_web_url().rstrip("/")
    app = modal.App.lookup(APP_NAME)
    app_id = app.app_id
    try:
        yield {"url": url, "admin": admin}
    finally:
        # Stop the app first, so an operation still running can't create a sandbox after the sweep.
        app.stop()

        # Sandbox.list still returns a sandbox for a few seconds after terminate(), so a sandbox
        # counts as running only while poll() has no exit code.
        def running():
            return [sb for sb in modal.Sandbox.list(app_id=app_id) if sb.poll() is None]

        left = running()
        log(f"teardown: {len(left)} sandbox(es) running in {APP_NAME}")
        for sb in left:
            sb.terminate()
        deadline = time.monotonic() + 60
        while (after := running()) and time.monotonic() < deadline:
            time.sleep(2)
        log(f"teardown: {len(after)} sandbox(es) running after terminate")
        assert after == [], "sandboxes left running"


def test_a_machine_created_through_the_controller_serves_t3_through_its_connect_url(controller):
    import modal

    url, admin = controller["url"], controller["admin"]

    # Q11: the first request after a deploy meets a cold container.
    t0 = time.monotonic()
    status, version = http("GET", f"{url}/version", token=admin)
    cold_s = time.monotonic() - t0
    t0 = time.monotonic()
    http("GET", f"{url}/version", token=admin)
    warm_s = time.monotonic() - t0
    log(f"Q11 cold start: GET /version took {cold_s:.2f} s cold and {warm_s:.2f} s warm")
    assert status == 200 and version["modal"] == "1.6.4"

    assert http("GET", f"{url}/version")[0] == 401
    assert http("GET", f"{url}/version", token="wrong")[0] == 401

    t_create = time.monotonic()
    t_create_wall = time.time()
    status, created = http("POST", f"{url}/machines", token=admin, body={"name": "it", "size": "small", "t3Version": T3_VERSION})
    assert status == 202, created
    op = {}
    while time.monotonic() - t_create < 15 * 60:
        status, op = http("GET", f"{url}/ops/{created['opId']}", token=admin)
        if op.get("status") != "running":
            break
        time.sleep(5)
    log(f"operation ended as {op.get('status')} after {time.monotonic() - t_create:.0f} s")
    assert op.get("status") == "done", op.get("error")

    status, machine = http("GET", f"{url}/machines/{created['id']}", token=admin)
    assert machine["power"] == "running"
    t3 = machine["endpoints"]["t3"]
    env_url = f"{t3['url']}/.well-known/t3/environment"
    with_token = env_url + "?" + urlencode({"_modal_connect_token": t3["token"]})

    t_t3 = time.monotonic()
    descriptor = None
    while time.monotonic() - t_t3 < 240:
        status, body = http("GET", with_token, timeout=15)
        if status == 200 and isinstance(body, dict):
            descriptor = body
            break
        time.sleep(2)
    log(f"T3 answered through the connect URL {time.monotonic() - t_t3:.1f} s after the operation ended")
    assert descriptor is not None and descriptor.get("environmentId"), descriptor

    assert http("GET", env_url, timeout=15)[0] == 401

    sb = modal.Sandbox.from_id(machine["sandboxId"])
    p = sb.exec("sh", "-c", "pid=$(cat /home/t3/.t3modal/t3.pid); ps -o user=,args= -p $pid; su - t3 -c 'sudo -n whoami'")
    p.wait()
    out = p.stdout.read()
    log(f"inside the sandbox: {out.strip()!r}")
    assert out.split()[0] == "t3"
    assert "--host 0.0.0.0 --port 3773" in out
    assert out.strip().endswith("root"), "sudo works only on the VM runtime (ADR 0004)"

    # #8: the machine secret reaches the sandbox's environment, and signs a heartbeat.
    secret = created["machineSecret"]
    assert secret not in json.dumps(machine)
    # init.sh is the process the daemon will start under in #9. On the VM runtime PID 1 is Modal's own init.
    p = sb.exec("sh", "-c", "pid=$(pgrep -o -f /opt/t3modal/init.sh); ps -o comm= -p 1; tr '\\0' '\\n' </proc/$pid/environ | grep '^T3MODAL_'")
    p.wait()
    pid1, *lines = p.stdout.read().splitlines()
    log(f"PID 1 is {pid1!r}, init.sh has {len(lines)} T3MODAL_ variables")
    env = dict(line.split("=", 1) for line in lines)
    in_env = env.get("T3MODAL_MACHINE_SECRET") == secret
    log(f"machine secret in the sandbox env: {in_env}, controller URL matches: {env.get('T3MODAL_CONTROLLER_URL') == url}")
    assert in_env
    assert env["T3MODAL_MACHINE_ID"] == created["id"]
    assert env["T3MODAL_CONTROLLER_URL"] == url

    from t3modal_controller.signing import sign

    def signed(method, path, body=b"", nonce=None):
        nonce = nonce or secrets.token_hex(16)
        ts = int(time.time())
        headers = {
            "X-T3modal-Machine": created["id"],
            "X-T3modal-Timestamp": str(ts),
            "X-T3modal-Nonce": nonce,
            "X-T3modal-Signature": sign(secret, method=method, path=path, timestamp=ts, nonce=nonce, body=body),
        }
        return http(method, url + path, body=body or None, headers=headers)

    hb_path = f"/machines/{created['id']}/heartbeat"
    hb = json.dumps({"sandboxId": machine["sandboxId"], "nextDueAt": None, "keepAwakeUntil": 0}).encode()
    nonce = secrets.token_hex(16)
    first, again = signed("POST", hb_path, hb, nonce), signed("POST", hb_path, hb, nonce)
    log(f"signed heartbeat: {first[0]}, same nonce again: {again[0]} {again[1]}")
    assert first[0] == 200
    assert again == (401, {"error": "unauthorized", "reason": "reused_nonce"})
    assert http("GET", f"{url}/machines/{created['id']}", token=admin)[1]["lastHeartbeatAt"] > 0
    assert signed("GET", f"/machines/{created['id']}")[0] == 403
    assert signed("GET", f"/ops/{created['opId']}")[0] == 200

    # #9: the daemon answers through its connect URL, sends heartbeats, and comes back after a crash.
    def run(script: str) -> str:
        p = sb.exec("sh", "-c", script)
        p.wait()
        return p.stdout.read() + p.stderr.read()

    def show_logs() -> None:
        log("daemon.log and supervisor.log:\n" + run("tail -n 20 /var/log/t3modal/daemon.log /var/log/t3modal/supervisor.log"))

    daemon = machine["endpoints"]["daemon"]
    status_url = f"{daemon['url']}/status?" + urlencode({"_modal_connect_token": daemon["token"]})

    def daemon_status(token=secret):
        return http("GET", status_url, token=token, timeout=15)

    t_d = time.monotonic()
    while (got := daemon_status())[0] != 200 and time.monotonic() - t_d < 60:
        time.sleep(2)
    log(f"daemon /status: {got[0]} {got[1] if got[0] != 200 else ''}")
    if got[0] != 200:
        show_logs()
    assert got[0] == 200
    # The daemon started during this test, so startedAt falls between the create and now.
    started_at = got[1]["startedAt"]
    assert isinstance(started_at, int) and (t_create_wall - 60) * 1000 <= started_at <= time.time() * 1000
    assert got == (200, {"machineId": created["id"], "sandboxId": machine["sandboxId"], "phase": "active", "startedAt": got[1]["startedAt"]})
    assert daemon_status(token=None) == (401, {"error": "unauthorized"})
    assert daemon_status(token="0" * 64) == (401, {"error": "unauthorized"})

    # T3 starts with an empty environment, so it doesn't get the machine secret.
    t3_env = run("tr '\\0' '\\n' </proc/$(cat /home/t3/.t3modal/t3.pid)/environ | grep -c '^T3MODAL_'").strip()
    log(f"T3 has {t3_env} T3MODAL_ variables")
    assert t3_env == "0"

    out = run(
        "su - t3 -c 'for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 1; done; "
        "id -nG; docker run --rm hello-world'"
    )
    log(f"docker as t3: groups {out.splitlines()[0]!r}, hello-world: {'Hello from Docker!' in out}")
    assert "Hello from Docker!" in out, out[-1500:]

    # The daemon's own heartbeats. Its first one can arrive before the operation records the sandbox
    # as running, and get 409 (§6.7), so this waits for two changes and times the gap between them.
    def last_heartbeat():
        return http("GET", f"{url}/machines/{created['id']}", token=admin)[1]["lastHeartbeatAt"]

    seen = [last_heartbeat()]
    t_hb = time.monotonic()
    while len(seen) < 3 and time.monotonic() - t_hb < 150:
        time.sleep(3)
        if (h := last_heartbeat()) != seen[-1]:
            seen.append(h)
    gap = (seen[-1] - seen[-2]) / 1000 if len(seen) == 3 else None
    log(f"daemon heartbeats: {len(seen) - 1} in {time.monotonic() - t_hb:.0f} s, gap {gap} s")
    if len(seen) < 3:
        show_logs()
    assert gap is not None and 55 <= gap <= 65

    # A crashed daemon comes back with a new PID, and the supervisor keeps running.
    before = run("cat /home/t3/.t3modal/daemon.pid").strip()
    run(f"kill -9 {before}")
    t_r = time.monotonic()
    after = before
    while time.monotonic() - t_r < 30:
        after = run("cat /home/t3/.t3modal/daemon.pid 2>/dev/null").strip()
        if after and after != before and daemon_status()[0] == 200:
            break
        time.sleep(1)
    log(f"daemon killed, back with a new PID after {time.monotonic() - t_r:.1f} s: {after != before}")
    assert after and after != before
    assert daemon_status()[0] == 200
    assert run("pgrep -c -f /opt/t3modal/init.sh").strip() != "0"
