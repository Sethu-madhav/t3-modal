# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.4"]
# ///
"""PROTOTYPE, throwaway. Phase 0 experiment 1: memory restore and the 24-hour clock.

Answers, from implementation.md §18:
- Q5: do T3 and a long-running process survive a memory snapshot and restore?
      does snapshot_filesystem work on a runtime="vm" sandbox?
      does a restore get a new timeout, or keep the original deadline?
- Q2: how long do a memory snapshot, a restore and a fresh boot take?
- Q8: what does an ended sandbox's tunnel URL answer?

The sandbox gets a 6-minute timeout. After about 2 minutes it takes a disk
snapshot, then a memory snapshot. The script waits 90 seconds, restores, and
watches when the restored sandbox ends:
- at create + 360 s: the wall-clock deadline carries over
- at restore + (360 - time run before the snapshot): running time carries over
- at restore + 360 s: the clock resets

Images get ttl=3600 so they expire within an hour. Cost is about $0.10.
Run: uv run experiments/phase-0/exp01_memory_restore.py
"""

import time

import modal

from common import START, TAG, app, http, image, log, save, sh, wait_for_t3

LIFETIME = 360
RUN_BEFORE_SNAPSHOT = 120
GAP_BEFORE_RESTORE = 90

result: dict = {"experiment": "exp01_memory_restore", "lifetime_s": LIFETIME, "steps": {}}
steps = result["steps"]
live: list[modal.Sandbox] = []


def main() -> None:
    log("creating VM sandbox with memory snapshots enabled")
    t_create = time.time()
    sb = modal.Sandbox.create(
        "sh", "-c", START,
        app=app, image=image, runtime="vm", _experimental_enable_snapshot=True,
        cpu=2, memory=4096, timeout=LIFETIME, encrypted_ports=[3773], tags={TAG: "exp01"},
    )
    live.append(sb)
    url1 = sb.tunnels()[3773].url
    boot_s, env1 = wait_for_t3(url1)
    steps["create"] = {"sandbox_id": sb.object_id, "t3_ready_s": round(time.time() - t_create, 1), "tunnel": url1,
                       "environment_id": env1.get("environmentId") or env1}
    log(f"T3 answered after {steps['create']['t3_ready_s']} s from create")

    pids_before = {
        "t3": sh(sb, "cat /root/p0/t3.pid"),
        "ticker": sh(sb, "cat /root/p0/ticker.pid"),
        "boot_id": sh(sb, "cat /proc/sys/kernel/random/boot_id"),
        "uptime": sh(sb, "cat /proc/uptime"),
    }
    steps["before"] = pids_before
    log(f"before snapshot: {pids_before}")

    wait = RUN_BEFORE_SNAPSHOT - (time.time() - t_create)
    if wait > 0:
        log(f"running {wait:.0f} s more before snapshotting")
        time.sleep(wait)
    sh(sb, "sqlite3 /root/.t3/userdata/statev2.sqlite '.backup /root/p0/statev2.backup.sqlite' && sync")

    log("disk snapshot on the VM sandbox")
    t = time.time()
    try:
        img = sb.snapshot_filesystem(timeout=600, ttl=3600)
        steps["disk_snapshot"] = {"ok": True, "image_id": img.object_id, "seconds": round(time.time() - t, 1)}
    except Exception as e:
        img = None
        steps["disk_snapshot"] = {"ok": False, "error": repr(e), "seconds": round(time.time() - t, 1)}
    log(f"disk snapshot: {steps['disk_snapshot']}")

    log("memory snapshot, which ends the sandbox")
    t_mem = time.time()
    snap = sb._experimental_snapshot()
    ran_before = t_mem - t_create
    steps["memory_snapshot"] = {"snapshot_id": snap.object_id, "seconds": round(time.time() - t_mem, 1),
                                "ran_before_s": round(ran_before, 1), "original_poll": sb.poll()}
    log(f"memory snapshot: {steps['memory_snapshot']}")

    # Q8: what the old tunnel answers once its sandbox is gone.
    answers = []
    for delay in (0, 20):
        time.sleep(delay)
        status, body = http(f"{url1}/.well-known/t3/environment", timeout=15)
        answers.append({"after_s": round(time.time() - t_mem, 1), "status": status, "body": body[:300]})
    steps["old_tunnel"] = answers
    log(f"old tunnel answers: {answers}")

    remaining = GAP_BEFORE_RESTORE - (time.time() - t_mem)
    if remaining > 0:
        log(f"waiting {remaining:.0f} s before restoring")
        time.sleep(remaining)

    log("memory restore")
    t_restore = time.time()
    sb2 = modal.Sandbox._experimental_from_snapshot(snap)
    live.append(sb2)
    url2 = sb2.tunnels()[3773].url
    ready_s, env2 = wait_for_t3(url2)
    after = {
        "t3_pid": sh(sb2, "cat /root/p0/t3.pid"),
        "ticker_pid": sh(sb2, "cat /root/p0/ticker.pid"),
        "t3_alive": sh(sb2, "kill -0 $(cat /root/p0/t3.pid) && echo yes || echo no"),
        "ticker_alive": sh(sb2, "kill -0 $(cat /root/p0/ticker.pid) && echo yes || echo no"),
        "boot_id": sh(sb2, "cat /proc/sys/kernel/random/boot_id"),
        "uptime": sh(sb2, "cat /proc/uptime"),
        "tick_gap_s": sh(sb2, "tail -n 400 /root/p0/ticks | awk 'NR>1 && $1-p>m {m=$1-p} {p=$1} END {print m}'"),
    }
    steps["restore"] = {"sandbox_id": sb2.object_id, "t3_ready_s": round(time.time() - t_restore, 1),
                        "tunnel": url2, "same_tunnel": url1 == url2,
                        "environment_id_same": env1.get("environmentId") == env2.get("environmentId"), **after}
    log(f"restore: {steps['restore']}")

    if img is not None:
        log("fresh boot from the disk image, default runtime")
        t = time.time()
        sb3 = modal.Sandbox.create("sh", "-c", START, app=app, image=img, cpu=1, memory=2048, timeout=300,
                                   encrypted_ports=[3773], tags={TAG: "exp01-fresh"})
        live.append(sb3)
        url3 = sb3.tunnels()[3773].url
        try:
            _, env3 = wait_for_t3(url3, limit=180)
            steps["fresh_boot"] = {
                "ok": True, "t3_ready_s": round(time.time() - t, 1),
                "environment_id_same": env1.get("environmentId") == env3.get("environmentId"),
                "backup_present": sh(sb3, "test -f /root/p0/statev2.backup.sqlite && echo yes || echo no"),
                "ticks_kept": sh(sb3, "wc -l < /root/p0/ticks"),
            }
        except Exception as e:
            steps["fresh_boot"] = {"ok": False, "error": repr(e), "log": sh(sb3, "tail -n 30 /root/p0/t3.log")}
        sb3.terminate()
        live.remove(sb3)
        log(f"fresh boot: {steps['fresh_boot']}")

    log("watching when the restored sandbox ends")
    deadline = t_restore + LIFETIME + 120
    while time.time() < deadline and sb2.poll() is None:
        time.sleep(5)
    t_end = time.time()
    ended = sb2.poll() is not None
    steps["restored_end"] = {
        "ended": ended,
        "since_create_s": round(t_end - t_create, 1),
        "since_restore_s": round(t_end - t_restore, 1),
        "expected_if_wall_clock_carries": LIFETIME,
        "expected_if_running_time_carries": round(LIFETIME - ran_before, 1),
        "expected_if_reset": LIFETIME,
        "returncode": sb2.poll(),
    }
    if ended:
        live.remove(sb2)
    since_create, since_restore = t_end - t_create, t_end - t_restore
    if not ended:
        verdict = "did not end in the window; the restore probably got a longer timeout"
    elif abs(since_create - LIFETIME) < 30:
        verdict = "wall-clock deadline carries over from the first create"
    elif abs(since_restore - (LIFETIME - ran_before)) < 30:
        verdict = "running time carries over across the snapshot"
    elif abs(since_restore - LIFETIME) < 30:
        verdict = "a restore resets the timeout"
    else:
        verdict = "unclear; read the numbers"
    result["clock_verdict"] = verdict
    log(f"restored sandbox end: {steps['restored_end']}")
    log(f"VERDICT: {verdict}")


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
        save("exp01", result)
