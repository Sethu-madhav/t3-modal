# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.4"]
# ///
"""PROTOTYPE, throwaway. Phase 0 experiment 7: agent sign-ins on a machine.

Answers, from implementation.md §18:
- Q13: with only CLAUDE_CODE_OAUTH_TOKEN set, does Claude Code work as user
       t3, and does T3 show Claude Code as signed in?
- Q14: does T3's Antigravity Google sign-in finish on a Modal machine by
       pasting the redirect URL, and does it survive a disk sleep?

Steps, each a separate run so the user can act in between:
  start <token-file>  create the sandbox, check Claude Code, print the pairing link
  sleep-wake          disk snapshot, end the sandbox, fresh boot, print a new pairing link
  link                print a pairing link to paste by hand
  stop                end every exp07 sandbox

The token is read from a file and passed as an ephemeral Modal Secret. It is
never printed. Pairing links print to the terminal for the user to paste and
never go to results/. A failed start or sleep-wake ends its sandbox.
Cost is about $0.06 an hour while running.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import modal

from common import TAG, app, log, save, sh, wait_for_t3

STATE = Path(tempfile.gettempdir()) / "t3modal-exp07-state.json"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "ca-certificates", "git", "sqlite3", "procps", "sudo", "bsdutils")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs",
        "useradd -m -s /bin/bash t3 && echo 't3 ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/t3",
        "su - t3 -c 'curl -fsSL https://t3.codes/install.sh | T3CODE_CHANNEL=nightly sh'",
        "npm i -g @anthropic-ai/claude-code",
        "su - t3 -c 'git config --global user.name t3modal && git config --global user.email t3modal@localhost'",
    )
    # Debian's xdg-open runs T3's quoted BROWSER command without a shell and fails.
    .add_local_file(Path(__file__).parent / "xdg-open-shim.sh", "/usr/local/bin/xdg-open", copy=True)
    .run_commands("chmod 755 /usr/local/bin/xdg-open")
)

# DISPLAY=:0 is a dummy. Without it the antigravity binary refuses to call T3's
# BROWSER helper, so T3's in-app Google sign-in fails with "No browser is available".
AS_T3 = "runuser -u t3 -- env HOME=/home/t3 USER=t3 DISPLAY=:0 PATH=/home/t3/.local/bin:/usr/local/bin:/usr/bin:/bin"
START = (f"{AS_T3} sh -c 'cd /home/t3 && t3 serve --host 0.0.0.0 --port 3773 > /home/t3/t3.log 2>&1' "
         "& sleep infinity")


def create(img, token: str) -> modal.Sandbox:
    secret = modal.Secret.from_dict({"CLAUDE_CODE_OAUTH_TOKEN": token})
    return modal.Sandbox.create("sh", "-c", START, app=app, image=img, runtime="vm", secrets=[secret],
                                cpu=(0.25, 4), memory=(1024, 8192), timeout=3600,
                                encrypted_ports=[3773], tags={TAG: "exp07"})


def pairing_url(sb: modal.Sandbox, url: str) -> str:
    link = sh(sb, f"{AS_T3} t3 auth pairing create --base-url {url} --label t3modal-phase0 --ttl 30m --json")
    pairing_url = json.loads(link[link.index("{"):])
    pairing_url = pairing_url.get("pairingUrl") or pairing_url.get("url") or next(
        v for v in pairing_url.values() if isinstance(v, str) and "/pair" in v)
    return pairing_url


def pair(sb: modal.Sandbox, url: str) -> None:
    # The t3code://pair deep link did nothing in T3 Code (Nightly) 0.1.11, so print
    # the link for the user to paste into Settings, Environments, Add environment.
    print("PAIRING LINK:", pairing_url(sb, url))


def antigravity_files(sb) -> str:
    return sh(sb, "find /home/t3/.t3/userdata/providers/antigravity -name acp_token.json 2>/dev/null | wc -l")


def start(token_file: str) -> None:
    token = Path(token_file).read_text().strip()
    sb = create(image, token)
    try:
        _start_checks(sb, token_file)
    except BaseException:
        sb.terminate()
        raise


def _start_checks(sb: modal.Sandbox, token_file: str) -> None:
    url = sb.tunnels()[3773].url
    wait_for_t3(url)
    log(f"T3 is up in {sb.object_id}")
    rec = {
        "anthropic_api_key_set": sh(sb, "[ -n \"$ANTHROPIC_API_KEY\" ] && echo yes || echo no"),
        "token_env_set": sh(sb, "[ -n \"$CLAUDE_CODE_OAUTH_TOKEN\" ] && echo yes || echo no"),
        "claude_prompt_as_t3": sh(sb, f"{AS_T3} claude -p 'Reply with exactly: ok' 2>&1 | tail -3"),
        "t3_version": sh(sb, f"{AS_T3} t3 --version"),
    }
    log(f"checks: {rec}")
    STATE.write_text(json.dumps({"sandbox_id": sb.object_id, "token_file": token_file}))
    pair(sb, url)
    save("exp07-start", rec)


def sleep_wake() -> None:
    state = json.loads(STATE.read_text())
    sb = modal.Sandbox.from_id(state["sandbox_id"])
    rec = {"antigravity_token_files_before": antigravity_files(sb)}
    # Match only the t3 user's processes. The sandbox's root start command also
    # contains "t3 serve", and killing it ends the sandbox with exit code 143.
    sh(sb, "pkill -u t3 -f 't3 serve'; sleep 2; sync")
    t = time.time()
    img = sb.snapshot_filesystem(timeout=600, ttl=3600)
    rec["snapshot_s"] = round(time.time() - t, 1)
    sb.terminate()
    token = Path(state["token_file"]).read_text().strip()
    t = time.time()
    sb2 = create(img, token)
    try:
        url = sb2.tunnels()[3773].url
        wait_for_t3(url)
        rec["fresh_boot_s"] = round(time.time() - t, 1)
        rec["antigravity_token_files_after"] = antigravity_files(sb2)
        log(f"after the disk sleep: {rec}")
        STATE.write_text(json.dumps({**state, "sandbox_id": sb2.object_id}))
        pair(sb2, url)
    except BaseException:
        sb2.terminate()
        raise
    save("exp07-sleep-wake", rec)


def link() -> None:
    """Print a pairing link to paste into T3 Code by hand, for when the deep link does nothing."""
    sb = modal.Sandbox.from_id(json.loads(STATE.read_text())["sandbox_id"])
    print(pairing_url(sb, sb.tunnels()[3773].url))


def stop() -> None:
    for sb in modal.Sandbox.list(app_id=app.app_id, tags={TAG: "exp07"}):
        sb.terminate()
        log(f"ended {sb.object_id}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "start":
        start(sys.argv[2])
    elif cmd == "sleep-wake":
        sleep_wake()
    elif cmd == "link":
        link()
    elif cmd == "stop":
        stop()
