# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.4"]
# ///
"""PROTOTYPE, throwaway. Phase 0 experiment 4: a chain of 50 disk snapshots.

Answers, from implementation.md §18:
- Q3: does each snapshot stack on the previous one, and does a long chain slow
      boots or snapshots, or hit a limit?
- §4.1: if the controller keeps only the two newest images and deletes older
      ones with image_delete, do later generations still boot?

Each generation boots from the previous image, appends 20 MB of new data and
one line to a log, snapshots, and ends. Images older than the newest two are
deleted as the chain grows. At the end the newest image boots and the script
checks every generation's data. Cost is about $0.10.
Run: uv run experiments/phase-0/exp04_snapshot_chain.py [generations]
"""

import sys
import time

import modal
import modal.experimental

from common import START, TAG, app, image, log, save, sh, wait_for_t3

GENERATIONS = int(sys.argv[1]) if len(sys.argv) > 1 else 50

result: dict = {"experiment": "exp04_snapshot_chain", "generations": []}
live: list[modal.Sandbox] = []


def main() -> None:
    current = image
    kept: list[str] = []
    for g in range(1, GENERATIONS + 1):
        t = time.time()
        sb = modal.Sandbox.create("sh", "-c", START, app=app, image=current, runtime="vm",
                                  cpu=1, memory=2048, timeout=600, encrypted_ports=[3773], tags={TAG: "exp04"})
        live.append(sb)
        wait_for_t3(sb.tunnels()[3773].url, limit=300)
        boot_s = round(time.time() - t, 1)
        sh(sb, f"mkdir -p /root/chain && head -c 20M /dev/urandom > /root/chain/g{g} "
               f"&& echo {g} >> /root/chain/log && sync")
        t = time.time()
        img = sb.snapshot_filesystem(timeout=600, ttl=7200)
        snap_s = round(time.time() - t, 1)
        sb.terminate()
        live.remove(sb)

        deleted = None
        kept.insert(0, img.object_id)
        if len(kept) > 2:
            deleted = kept.pop()
            try:
                modal.experimental.image_delete(deleted)
            except Exception as e:
                deleted = f"failed {deleted}: {e!r}"
        current = img
        rec = {"g": g, "boot_s": boot_s, "snapshot_s": snap_s, "image_id": img.object_id, "deleted": deleted}
        result["generations"].append(rec)
        log(f"generation {rec}")

    log("final boot from the newest image")
    t = time.time()
    sb = modal.Sandbox.create("sh", "-c", START, app=app, image=current, runtime="vm",
                              cpu=1, memory=2048, timeout=600, encrypted_ports=[3773], tags={TAG: "exp04-final"})
    live.append(sb)
    wait_for_t3(sb.tunnels()[3773].url, limit=300)
    result["final"] = {
        "boot_s": round(time.time() - t, 1),
        "generation_files": sh(sb, "ls /root/chain | grep -c '^g'"),
        "log_lines": sh(sb, "wc -l < /root/chain/log"),
        "log_in_order": sh(sb, f"seq 1 {GENERATIONS} | cmp -s - /root/chain/log && echo yes || echo no"),
    }
    log(f"final: {result['final']}")

    boots = [r["boot_s"] for r in result["generations"]]
    snaps = [r["snapshot_s"] for r in result["generations"]]
    tenth = max(1, len(boots) // 10)
    result["trend"] = {
        "boot_first_10pct_avg": round(sum(boots[:tenth]) / tenth, 1),
        "boot_last_10pct_avg": round(sum(boots[-tenth:]) / tenth, 1),
        "snapshot_first_10pct_avg": round(sum(snaps[:tenth]) / tenth, 1),
        "snapshot_last_10pct_avg": round(sum(snaps[-tenth:]) / tenth, 1),
    }
    log(f"trend: {result['trend']}")


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
        save("exp04", result)
