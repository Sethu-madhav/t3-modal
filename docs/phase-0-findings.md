# Phase 0 findings

Results of the Phase 0 experiments, measured on the user's Modal Starter workspace on 2026-11-18 with Modal SDK 1.6.4 and T3 0.1.10. The scripts and raw JSON results live on the `phase-0` branch under `experiments/phase-0/`. What docs and source code say is in [phase-0-research.md](phase-0-research.md).

## Summary

| # | Question | Answer | Effect on the plan |
|---|---|---|---|
| 5 | Do T3 and other processes survive a memory snapshot and restore? | Yes. Same PIDs, same boot ID, and the kernel's uptime continues. T3 answered 1.7 s after the restore call | Memory sleep works as §4.4 describes |
| 5 | Does `snapshot_filesystem` work on a `runtime="vm"` sandbox? | Yes, in 3.8 s on a small disk. A default-runtime sandbox booted from that image | One runtime for every machine: VM |
| 5 | Does a restore get a new timeout? | Yes. The restored sandbox ended 366 s after the restore, against a 360 s timeout | Rollover counts from the latest wake, as §9.6 assumes |
| 2 | How long does a wake take? | Memory restore: 1.7 s to T3 answering. Fresh boot from a cached image: 4.4 s | Much faster than the 30 s the plan allowed |
| 8 | What does an ended sandbox's tunnel answer? | No HTTP answer. The connection closes with nothing sent, both 4 s and 24 s after the end | The proxy treats a closed connection as "maybe asleep" and asks the controller (§9.4) |
| 16 | Does T3 work through a connect-token URL? | Yes, for HTTP and the WebSocket. Without the token Modal answers 401 | Use connect-token URLs instead of tunnels (see below) |
| 7 | Do idle WebSockets stay open? | Yes, 75 s idle through both routes, and a ping still worked | No keepalive needed at this scale. A longer idle test comes later |

Both experiments cost under $0.10 in total.

## Experiment 1: memory restore and the 24-hour clock

Script: `experiments/phase-0/exp01_memory_restore.py`. A VM sandbox with 2 cores, 4 GiB and a 360 s timeout ran T3 and a ticker that wrote a line every second.

| Step | Time |
|---|---|
| Create to T3 answering | 4.5 s |
| Disk snapshot of the running VM sandbox | 3.8 s |
| Memory snapshot, which ended the sandbox | 3.4 s |
| Memory restore to T3 answering | 1.7 s |
| Fresh boot from the disk image to T3 answering | 4.4 s |

After the restore:
- T3 kept PID 1027 and the ticker kept PID 1028. Both were alive.
- The boot ID matched, and `/proc/uptime` read 124.5 s. That is the 124 s the sandbox ran before the snapshot, so the VM's clock paused while it slept.
- The ticker's largest gap was 91 s, which matches the time between snapshot and restore.
- T3 reported the same environment ID through a new tunnel URL.

The fresh boot from the disk image kept the same environment ID, the SQLite backup and all 124 ticker lines.

The 24-hour clock: the script waited 90 s between snapshot and restore, so the three possible outcomes differ by at least 90 s. The restored sandbox ended 366.2 s after the restore and 580.3 s after the first create, with exit code 124. A restore therefore starts a new timeout, and the timeout comes from the original `Sandbox.create`.

The disk was small, a few hundred MB of changes, so the 3.8 s snapshot time is a lower bound. Question 1 measures large disks.

## Experiment 2: T3 through a connect-token URL

Script: `experiments/phase-0/exp02_connect_token.py`. A default-runtime sandbox ran T3 with `encrypted_ports=[3773]`. The script made a connect token with `create_connect_token(port=3773)` and minted a T3 session with `t3 auth session issue`.

| Request | Answer |
|---|---|
| Tunnel, no credentials, `/.well-known/t3/environment` | 200. Anyone with the URL reaches T3 |
| Connect URL, no Modal token | 401 "No auth token provided" |
| Connect URL, Modal token as `_modal_connect_token` query parameter | 200 |
| Connect URL, Modal token in `Authorization` | 200 |
| Connect URL, query token plus T3's own bearer, `/api/auth/session` | 200, `authenticated: true` |
| Connect URL, Modal token in `Authorization` and no T3 bearer | 200, `authenticated: false`. T3 sees no session, so the header can't carry both tokens |
| WebSocket ticket through the connect URL | Issued |
| WebSocket `/ws` through the connect URL, query token | Opened. Stayed open 75 s idle, then answered a ping |
| WebSocket `/ws` through the tunnel | The same |
| WebSocket through the connect URL with no Modal token | 401 |

T3's `/ws` answers 426 unless the URL carries `orchestrationProtocol=2`. The laptop proxy must forward T3 Code's query parameters unchanged and add `_modal_connect_token`.

## What changes in the plan

1. **Connect-token URLs replace public tunnels.** Each wake creates connect tokens for ports 3773 and 3774. The sandbox opens no `encrypted_ports`. Modal then turns away any request without the token, so the T3 environment and the daemon are no longer reachable by anyone who learns a URL. The proxy sends the token as a query parameter, because T3 needs the `Authorization` header for its own bearer. §5, §6.2, §9.2, §10 and §15.1 change. Still to check: how long a connect token lasts, and whether one sandbox can hold two tokens for different ports.
2. **Every machine runs on `runtime="vm"`.** Memory sleeps need it, disk snapshots work on it, and the research found Docker works on it.
3. **Wakes are fast.** A fresh boot took 4.4 s and a memory restore 1.7 s. The proxy's 90 s hold (§10.4) has plenty of margin.
4. **Rollover stays as planned.** Each wake gets a full timeout, so a machine that sleeps once a day never reaches the 24-hour limit.

## Still to run

Questions 1, 3, 4, 6, 9, 13 and 14, plus the parts of 7, 10 and 11 that need measuring. The next batch:
- Snapshot time with 5 to 20 GB of changes (question 1).
- 50 generations of chained disk snapshots (question 3).
- CPU above the request with `(request, limit)` pairs (question 6).
- gVisor and VM checks: `/proc/stat`, `node:sqlite`, `script` and `sudo` (question 9).
