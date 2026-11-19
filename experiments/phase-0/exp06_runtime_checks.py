# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.4"]
# ///
"""PROTOTYPE, throwaway. Phase 0 experiment 6: what works inside a sandbox.

Answers, from implementation.md §18:
- Q9: /proc/stat and cgroup CPU accounting, node:sqlite on Node 22, `script`
      with a terminal, sudo for a non-root user, and Docker, on both runtimes.
- Q10: does `t3 serve` run as a non-root user without systemd, and what does
       it log about keep-awake?
- Claude Code installs and runs as the non-root user.

Cost is about $0.05.
Run: uv run experiments/phase-0/exp06_runtime_checks.py
"""

import time

import modal

from common import TAG, app, image, log, save, sh, wait_for_t3

full = (
    image.apt_install("sudo", "bsdutils", "docker.io")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs",
        "useradd -m -s /bin/bash t3 && echo 't3 ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/t3",
        "su - t3 -c 'curl -fsSL https://t3.codes/install.sh | sh'",
        "npm i -g @anthropic-ai/claude-code",
    )
)

START_AS_T3 = "su - t3 -c 't3 serve --host 0.0.0.0 --port 3773 > /home/t3/t3.log 2>&1' & sleep infinity"

CHECKS = {
    "proc_stat_cpu_line": "head -1 /proc/stat",
    "proc_stat_changes": "a=$(head -1 /proc/stat); sleep 2; b=$(head -1 /proc/stat); [ \"$a\" != \"$b\" ] && echo yes || echo no",
    "cgroup_cpu_stat": "head -3 /sys/fs/cgroup/cpu.stat 2>&1 | tr '\\n' ' '",
    "node_version": "node --version",
    "node_sqlite": "node -e \"const {DatabaseSync}=require('node:sqlite');const d=new DatabaseSync(':memory:');"
                   "console.log(d.prepare('select 1 as x').get().x)\" 2>&1 | tail -1",
    "script_tty": "script -qec 'tty' /dev/null 2>&1 | tr -d '\\r'",
    "sudo_as_t3": "su - t3 -c 'sudo -n whoami' 2>&1",
    "t3_runs_as": "ps -o user= -C t3 2>/dev/null | sort -u | tr '\\n' ' '; ps -eo user,args | grep '[t]3 serve' | head -1",
    "t3_keepawake_log": "grep -ci 'keep-awake\\|systemd' /home/t3/t3.log; grep -i 'keep-awake\\|systemd' /home/t3/t3.log | head -2",
    "claude_version_as_t3": "su - t3 -c 'claude --version' 2>&1 | tail -1",
    "docker": "(dockerd > /tmp/dockerd.log 2>&1 &) ; for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done; "
              "docker run --rm hello-world 2>&1 | grep -m1 'Hello from Docker' || (echo FAILED; tail -3 /tmp/dockerd.log)",
}


def run(runtime):
    kw = {"runtime": runtime} if runtime else {}
    sb = modal.Sandbox.create("sh", "-c", START_AS_T3, app=app, image=full, cpu=2, memory=4096, timeout=900,
                              encrypted_ports=[3773], tags={TAG: "exp06"}, **kw)
    rec = {"runtime": runtime or "default"}
    try:
        t = time.time()
        wait_for_t3(sb.tunnels()[3773].url, limit=180)
        rec["t3_ready_as_non_root_s"] = round(time.time() - t, 1)
        time.sleep(20)
        for name, cmd in CHECKS.items():
            rec[name] = sh(sb, cmd)
            log(f"{rec['runtime']} {name}: {rec[name]}")
    except Exception as e:
        rec["error"] = repr(e)
        log(f"{rec['runtime']} ERROR: {e!r}")
    finally:
        sb.terminate()
    return rec


if __name__ == "__main__":
    result = {"experiment": "exp06_runtime_checks", "runs": []}
    try:
        for rt in ("vm", None):
            result["runs"].append(run(rt))
    except Exception as e:
        result["error"] = repr(e)
    finally:
        save("exp06", result)
