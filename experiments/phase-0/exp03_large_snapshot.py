# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.4"]
# ///
"""PROTOTYPE, throwaway. Phase 0 experiment 3: snapshot time on a large disk.

Answers, from implementation.md §18:
- Q1: how long does snapshot_filesystem take with 5, 10 and 20 GB of changes,
      and with many small files like node_modules?
- Q2: how long does a fresh boot from a 20 GB image take, and how fast does
      the restored disk read?

Random data is used so compression can't hide the size. Each step adds data
to the same running VM sandbox and snapshots it. Images get ttl=3600.
Cost is about $0.15.
Run: uv run experiments/phase-0/exp03_large_snapshot.py
"""

import time

import modal

from common import START, TAG, app, image, log, save, sh, wait_for_t3

result: dict = {"experiment": "exp03_large_snapshot", "steps": []}
live: list[modal.Sandbox] = []


def timed_sh(sb, cmd):
    t = time.time()
    out = sh(sb, cmd)
    return round(time.time() - t, 1), out


def snapshot(sb, label):
    t = time.time()
    try:
        img = sb.snapshot_filesystem(timeout=1200, ttl=3600)
        rec = {"label": label, "ok": True, "seconds": round(time.time() - t, 1), "image_id": img.object_id}
    except Exception as e:
        img = None
        rec = {"label": label, "ok": False, "seconds": round(time.time() - t, 1), "error": repr(e)}
    rec["disk_used"] = sh(sb, "df -h / | tail -1")
    result["steps"].append(rec)
    log(f"snapshot: {rec}")
    return img


def main() -> None:
    sb = modal.Sandbox.create("sh", "-c", START, app=app, image=image, runtime="vm",
                              cpu=2, memory=4096, timeout=3600, tags={TAG: "exp03"})
    live.append(sb)
    log(f"sandbox {sb.object_id}")
    sh(sb, "mkdir -p /root/big")

    img = None
    for gb, total in ((5, 5), (5, 10), (10, 20)):
        secs, _ = timed_sh(sb, f"for i in $(seq 1 {gb}); do head -c 1G /dev/urandom > /root/big/f{total}-$i; done; sync")
        log(f"wrote {gb} GB in {secs} s, {total} GB total")
        img = snapshot(sb, f"{total} GB random")

    secs, out = timed_sh(sb, "python3 -c \"import os\n"
                             "for d in range(200):\n"
                             "  p=f'/root/small/{d}'; os.makedirs(p, exist_ok=True)\n"
                             "  for f in range(1000): open(f'{p}/{f}.js','wb').write(os.urandom(4096))\"; sync; "
                             "find /root/small -type f | wc -l")
    log(f"wrote {out} small files in {secs} s")
    img = snapshot(sb, "20 GB random plus 200k files of 4 KB")

    sb.terminate()
    live.remove(sb)

    if img is not None:
        log("fresh boot from the last image")
        t = time.time()
        sb2 = modal.Sandbox.create("sh", "-c", START, app=app, image=img, runtime="vm",
                                   cpu=2, memory=4096, timeout=1200, encrypted_ports=[3773], tags={TAG: "exp03-boot"})
        live.append(sb2)
        url = sb2.tunnels()[3773].url
        wait_for_t3(url, limit=600)
        boot = {"t3_ready_s": round(time.time() - t, 1)}
        boot["read_1gb_s"], _ = timed_sh(sb2, "cat /root/big/f20-1 > /dev/null")
        boot["read_all_20gb_s"], _ = timed_sh(sb2, "cat /root/big/* > /dev/null")
        boot["walk_200k_files_s"], out = timed_sh(sb2, "find /root/small -type f | wc -l")
        boot["files_found"] = out
        result["fresh_boot"] = boot
        log(f"fresh boot: {boot}")


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
        save("exp03", result)
