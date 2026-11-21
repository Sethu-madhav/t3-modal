"""Real-Modal test for #7: create a machine through the deployed controller and reach its T3.

Opt-in: T3MODAL_IT=1 uv run pytest tests/it -s
It deploys the controller as t3modal-controller-it, with its own Volume, Dict and admin Secret, and
creates one small machine. The first run builds the machine image, which takes several minutes.
Teardown stops the app, ends every sandbox in it, and checks that 0 are left.
Connect tokens and the admin token are never printed.
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


def http(method: str, url: str, *, token: str | None = None, body: dict | None = None, timeout: float = 60):
    """Returns (status, parsed JSON or text)."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
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
