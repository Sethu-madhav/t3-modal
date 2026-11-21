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
REVISION = "4"  # CONTROLLER_REVISION: bump on every controller change, so setup redeploys (§6.1)
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
nonces_dict = modal.Dict.from_name(f"t3modal-nonces{SUFFIX}", create_if_missing=True)
admin_secret = modal.Secret.from_name(f"t3modal-admin{SUFFIX}", required_keys=["ADMIN_TOKEN"])
hmac_secret = modal.Secret.from_name(f"t3modal-hmac{SUFFIX}", required_keys=["HMAC_KEY"])  # machine secrets (§6.7)


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
            "usermod -aG docker t3",  # docker without sudo; docker.io created the group above
            f"su - t3 -c 'curl -fsSL https://t3.codes/install.sh | T3CODE_VERSION={t3_version} sh'",
            f"npm i -g @anthropic-ai/claude-code@{CLAUDE_CODE_VERSION}",
            "git config --system user.name t3modal && git config --system user.email t3modal@localhost",
        )
        .add_local_dir(SANDBOX_DIR, "/opt/t3modal", copy=True)
    )


def credential_secret_name(kind: str) -> str:
    """The named Secret that holds one credential kind (§11.3)."""
    return f"t3modal-{kind}{SUFFIX}"


class ModalCloud:
    """The real Cloud: Modal's Sandbox and Secret APIs."""

    def create_sandbox(self, *, t3_version: str, args: dict, env: dict[str, str], credentials: list[str]) -> str:
        sb = modal.Sandbox.create(
            "tini", "--", "/bin/sh", "/opt/t3modal/init.sh",
            app=modal.App.lookup(APP_NAME),
            image=machine_image(t3_version),
            # The env Secret is ephemeral, so the machine secret is never stored in Modal.
            secrets=[modal.Secret.from_dict(env)] + [modal.Secret.from_name(credential_secret_name(k)) for k in credentials],
            **args,
        )
        return sb.object_id

    def put_credential(self, kind: str, values: dict[str, str]) -> None:
        # Delete then create, rather than update, so the Secret holds no stale key and its created_at
        # is the time it was set. A machine created between the two calls starts without it.
        name = credential_secret_name(kind)
        modal.Secret.objects.delete(name, allow_missing=True)
        modal.Secret.objects.create(name, values)

    def credentials(self) -> dict[str, int]:
        from t3modal_controller.controller import CREDENTIAL_KEYS

        kinds = {credential_secret_name(k): k for k in CREDENTIAL_KEYS}
        found = {}
        for secret in modal.Secret.objects.list():
            info = secret.info()
            if info.name in kinds:
                found[kinds[info.name]] = int(info.created_at.timestamp() * 1000)
        return found

    def delete_credential(self, kind: str) -> bool:
        try:
            modal.Secret.objects.delete(credential_secret_name(kind))
        except modal.exception.NotFoundError:
            return False
        return True

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


@app.function(image=controller_image, volumes={STATE_ROOT: state_volume}, secrets=[hmac_secret], timeout=15 * 60)
def run_op(op_id: str) -> None:
    """Runs one long operation, past the web request timeout (§6.3)."""
    from t3modal_controller.controller import Controller

    Controller(
        store=store(),
        ops=ops_dict,
        cloud=ModalCloud(),
        clock_ms=now_ms,
        hmac_key=os.environ["HMAC_KEY"],
        controller_url=api.get_web_url().rstrip("/"),
    ).perform(op_id)


@app.function(
    image=controller_image,
    volumes={STATE_ROOT: state_volume},
    secrets=[admin_secret, hmac_secret],
    scaledown_window=300,
    min_containers=0,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def api():
    from t3modal_controller.api import create_api

    return create_api(
        admin_token=os.environ["ADMIN_TOKEN"],
        hmac_key=os.environ["HMAC_KEY"],
        controller_url=api.get_web_url().rstrip("/"),
        store=store(),
        ops=ops_dict,
        nonces=nonces_dict,
        cloud=ModalCloud(),
        run_op=lambda op_id: run_op.spawn(op_id),
        revision=REVISION,
        modal_version=modal.__version__,
        clock_ms=now_ms,
    )
