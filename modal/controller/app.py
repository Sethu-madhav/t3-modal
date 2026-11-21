"""The t3modal controller: the API and timer deployed to the user's Modal workspace (implementation.md §6).

The logic lives in t3modal_controller, which pytest runs without Modal. This file connects it to Modal:
the images, the Volume, the Secret, the operations Dict, and ModalCloud, the real Cloud.

Deploy: modal deploy app.py. With T3MODAL_NAME_SUFFIX=-it, every name gets the suffix, so the
real-Modal test suite never touches the real controller, its state or its admin token.
"""

import os
import time
from pathlib import Path

import modal

SUFFIX = os.environ.get("T3MODAL_NAME_SUFFIX", "")
if SUFFIX not in ("", "-it"):
    raise ValueError(f"T3MODAL_NAME_SUFFIX must be empty or -it, not {SUFFIX!r}")

APP_NAME = f"t3modal-controller{SUFFIX}"
REVISION = "1"  # CONTROLLER_REVISION: bump on every controller change, so setup redeploys (§6.1)
CLAUDE_CODE_VERSION = "2.1.329"  # pinned per release (§7.1)
STATE_ROOT = "/state"
# The sandbox scripts sit next to this folder in the repo. Inside the controller's container, the
# image copies them to /root/sandbox and sets T3MODAL_SANDBOX_DIR, so machine images build there too.
SANDBOX_DIR = Path(os.environ.get("T3MODAL_SANDBOX_DIR", Path(__file__).resolve().parent.parent / "sandbox"))

app = modal.App(APP_NAME)

controller_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(str(Path(__file__).resolve().parent / "requirements.lock"))
    .env({"T3MODAL_NAME_SUFFIX": SUFFIX, "T3MODAL_SANDBOX_DIR": "/root/sandbox"})
    .add_local_dir(SANDBOX_DIR, "/root/sandbox")
    .add_local_python_source("t3modal_controller")
)

state_volume = modal.Volume.from_name(f"t3modal-state{SUFFIX}", create_if_missing=True, version=2)
ops_dict = modal.Dict.from_name(f"t3modal-ops{SUFFIX}", create_if_missing=True)
admin_secret = modal.Secret.from_name(f"t3modal-admin{SUFFIX}", required_keys=["ADMIN_TOKEN"])


def machine_image(t3_version: str) -> modal.Image:
    """The base image for machines (§7.1). Modal caches it by its definition, so one T3 version builds once."""
    from t3modal_controller.controller import check_t3_version

    check_t3_version(t3_version)  # it goes into a shell command below
    return (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install(
            "git", "curl", "ca-certificates", "build-essential", "sqlite3", "ripgrep", "jq", "unzip",
            "openssh-client", "tini", "sudo", "procps", "gh", "docker.io",
        )
        .run_commands(
            "curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs",
            "useradd -m -s /bin/bash t3 && echo 't3 ALL=(ALL) NOPASSWD:ALL' >/etc/sudoers.d/t3",
            f"su - t3 -c 'curl -fsSL https://t3.codes/install.sh | T3CODE_VERSION={t3_version} sh'",
            f"npm i -g @anthropic-ai/claude-code@{CLAUDE_CODE_VERSION}",
            "git config --system user.name t3modal && git config --system user.email t3modal@localhost",
        )
        .add_local_dir(SANDBOX_DIR, "/opt/t3modal", copy=True)
    )


class ModalCloud:
    """The real Cloud: Modal's Sandbox API."""

    def create_sandbox(self, *, t3_version: str, args: dict) -> str:
        sb = modal.Sandbox.create(
            "tini", "--", "/bin/sh", "/opt/t3modal/init.sh",
            app=modal.App.lookup(APP_NAME),
            image=machine_image(t3_version),
            **args,
        )
        return sb.object_id

    def connect(self, sandbox_id: str, port: int):
        from t3modal_controller.machines import Endpoint

        creds = modal.Sandbox.from_id(sandbox_id).create_connect_token(port=port)
        return Endpoint(url=creds.url.rstrip("/"), token=creds.token)

    def terminate(self, sandbox_id: str) -> None:
        modal.Sandbox.from_id(sandbox_id).terminate()


def now_ms() -> int:
    return int(time.time() * 1000)


def store():
    from t3modal_controller.store import StateStore

    return StateStore(Path(STATE_ROOT), state_volume)


@app.function(image=controller_image, volumes={STATE_ROOT: state_volume}, timeout=15 * 60)
def run_op(op_id: str) -> None:
    """Runs one long operation, past the web request timeout (§6.3)."""
    from t3modal_controller.controller import Controller

    Controller(store=store(), ops=ops_dict, cloud=ModalCloud(), clock_ms=now_ms).perform(op_id)


@app.function(
    image=controller_image,
    volumes={STATE_ROOT: state_volume},
    secrets=[admin_secret],
    scaledown_window=300,
    min_containers=0,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def api():
    from t3modal_controller.api import create_api

    return create_api(
        admin_token=os.environ["ADMIN_TOKEN"],
        store=store(),
        ops=ops_dict,
        cloud=ModalCloud(),
        run_op=lambda op_id: run_op.spawn(op_id),
        revision=REVISION,
        modal_version=modal.__version__,
        clock_ms=now_ms,
    )
