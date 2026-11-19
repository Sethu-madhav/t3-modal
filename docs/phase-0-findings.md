# Phase 0 findings

Results of the Phase 0 experiments, measured on the user's Modal Starter workspace on 2026-11-18 with Modal SDK 1.6.4 and T3 0.1.10. Experiment 7 used T3 0.1.11-nightly. The scripts and raw JSON results live on the `phase-0` branch under `experiments/phase-0/`. What docs and source code say is in [phase-0-research.md](phase-0-research.md).

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
| 1 | How long does a disk snapshot take? | 9 s for 5 GB, 9 s for 10 GB, 15 s for 20 GB of new random data. 199 s for 200,000 new files of 4 KB | File count matters far more than size. The 600 s timeout holds |
| 2 | How fast is the disk after a fresh boot? | T3 answered in 6 s from a 22 GB image, but the first read of file contents ran at about 44 MB/s. Listing 200,000 files took 0.2 s | Modal loads image data lazily. The first build after a disk sleep is slow |
| 3 | Does a 50-image chain slow down or break? | No. Boots averaged 6.6 s in the first 5 generations and 4.6 s in the last 5. Snapshots took 3.4 s and 2.8 s. All 50 generations' data was present | Long chains are safe |
| 3 | Can older images be deleted while the chain grows? | Yes. Deleting all but the two newest images never broke a later boot | §4.1's two-image rule works |
| 6 | Can a sandbox use more than it requests? | Yes, on both runtimes. With `cpu=(0.25, 4)` four busy processes used 4.00 cores on VM and 3.93 on the default runtime. With `memory=(1024, 8192)` both allocated 6 GiB | Sizing mode B works |
| 12 | What does Modal bill, and can t3modal read it? | The larger of request and use, sampled. `app.sandboxes.stats(group_by_tags=...)` works on Starter and matched the expected cost. Per-sandbox `_experimental_stats()` is refused on Starter | Costs come from per-tag stats, tagged by machine |
| 9 | What works inside a sandbox? | See the table under experiment 6. Docker, `sudo` and cgroup CPU stats work only on `runtime="vm"` | VM runtime for every machine |
| 10 | Does `t3 serve` run as a non-root user without systemd? | Yes, on both runtimes. Its log had no keep-awake or systemd lines | §7.2 works as written |
| 13 | Does Claude Code work through T3 with only `CLAUDE_CODE_OAUTH_TOKEN`? | Yes. T3 showed Claude Code as authenticated, and a thread answered in 2.6 s with no sign-in step | §11's token route works as written |
| 14 | Does T3's Antigravity Google sign-in work on a machine? | No. Four separate blockers on a Linux machine with no screen. Three were worked around, and the fourth was still open when the user stopped the test | Antigravity leaves v1 |

Modal's per-tag stats put experiments 3 to 6 at $0.15. Experiments 1 and 2 ran before the stats window and cost about $0.06 by the rates, so all six cost about $0.21. The stats put experiment 7 at $0.035, so experiments 1 to 7 cost about $0.25.

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

## Experiment 3: snapshot time on a large disk

Script: `experiments/phase-0/exp03_large_snapshot.py`. One VM sandbox with 2 cores and 4 GiB. Each step added data and snapshotted the running sandbox.

| Disk changes since the base image | Write time | Snapshot time |
|---|---|---|
| 5 GB random | 12.8 s | 8.9 s |
| 10 GB random | 12.7 s for the second 5 GB | 9.1 s |
| 20 GB random | 25.2 s for the next 10 GB | 15.4 s |
| 20 GB plus 200,000 files of 4 KB, about 800 MB | 8.2 s | 198.9 s |

A fresh boot from the last image:
- T3 answered after 6.0 s.
- Reading one 1 GB file took 25.8 s. Reading all 20 GB took 462.7 s, about 44 MB/s.
- Listing all 200,000 files took 0.2 s.

Snapshot time grows with the number of files, not their size. An agent that runs `npm install` in a few projects can create 100,000 files, so a sleep after that may take minutes. Later sleeps only save what changed.

After a fresh boot, Modal fetches file contents the first time they are read. A large repo's first build or test run after a disk sleep pays for that. Memory restores may avoid it, which is a further reason to prefer them. Still to measure: whether a second read is fast, and whether a memory restore reads at full speed.

## Experiment 4: a chain of 50 disk snapshots

Script: `experiments/phase-0/exp04_snapshot_chain.py`. Each generation booted from the previous image, added 20 MB and one log line, snapshotted and ended. After each snapshot, every image but the newest two was deleted with `modal.experimental.image_delete`.

- All 50 generations booted and snapshotted. Nothing failed.
- Boot to T3 answering averaged 6.6 s in the first 5 generations and 4.6 s in the last 5.
- Snapshots averaged 3.4 s in the first 5 and 2.8 s in the last 5.
- A final boot from generation 50 found all 50 data files and all 50 log lines in order.
- 48 older images were deleted along the way, and no later boot needed them.

## Experiment 5: using more than the request

Script: `experiments/phase-0/exp05_cpu_burst.py`. Each sandbox had `cpu=(0.25, 4)` and `memory=(1024, 8192)`. It idled 60 s, ran 4 busy processes for 60 s, allocated and touched 6 GiB, and idled 60 s more.

| | VM runtime | Default runtime |
|---|---|---|
| Cores used during the burst | 4.00 | 3.93 |
| 6 GiB allocation | Worked | Worked |
| `nproc` | 4 | 4 |
| `MemTotal` at start | 2.0 GB, yet the 6 GiB allocation worked, so the VM grows its memory on demand | 8.0 GB |

Modal's per-tag stats charged $0.0214 of CPU for both sandboxes. Billing the larger of request and use predicts about 540 core-seconds, 0.25 cores while idle and 4 during the bursts. That costs $0.0213, so the rule holds. Idle time billed at the 0.25-core request.

`Sandbox._experimental_stats()` failed with "Sandbox stats are not enabled for this workspace". `app.sandboxes.stats(since=..., group_by_tags=[...])` worked and returned cost by CPU and memory per tag value.

## Experiment 6: what works inside a sandbox

Script: `experiments/phase-0/exp06_runtime_checks.py`. The image added Node 22, `sudo`, `docker.io`, Claude Code and a user `t3` with T3 installed. T3 ran as `t3`.

| Check | VM runtime | Default runtime |
|---|---|---|
| `t3 serve` as user `t3`, answering | Yes | Yes |
| `/proc/stat` CPU line, changing over time | Yes | Yes |
| cgroup `cpu.stat` | Yes | Missing |
| Node 22 `node:sqlite` | Works, with an experimental warning on stderr | The same |
| `script` gives a terminal | Yes, `/dev/pts/0` | Yes |
| `sudo` as user `t3` | Works | Fails, because the container sets "no new privileges" |
| Claude Code runs as `t3` | Yes, 2.1.328 | Yes |
| Docker, `docker run hello-world` | Works | Fails, because iptables has no nft support |
| Keep-awake or systemd lines in T3's log | None | None |

## Experiment 7: agent sign-ins

Script: `experiments/phase-0/exp07_signins.py`. The machine ran T3 0.1.11-nightly as user `t3` on `runtime="vm"`, with the user's `claude setup-token` token passed as an ephemeral Modal Secret. The user paired T3 Code (Nightly) on their Mac with the machine and worked in it.

**Claude Code works with only the token.** `ANTHROPIC_API_KEY` was unset. `claude -p` as `t3` answered, T3's Providers page showed Claude as authenticated, and a thread using Haiku answered "hi" 2.6 s after the message. The token never touched the disk.

**Antigravity's Google sign-in does not work.** T3 downloaded the Antigravity helper, 257 MB, into `~/.t3/tools/antigravity-acp` when the user clicked Install. Sign-in then failed in turn on each of these:

1. The `antigravity` binary checks for a graphical display and refuses to call T3's browser helper without one. A dummy `DISPLAY=:0` got past it.
2. It then opens the link with `xdg-open`, which the image lacked.
3. Debian's `xdg-open` runs T3's shell-quoted `BROWSER` command without a shell, so it fails with "'/usr/bin/node': not found". A 9-line replacement in `experiments/phase-0/xdg-open-shim.sh` fixed it in a direct test.
4. The user still saw no link in T3 Code, and stopped the test.

The terminal route, `antigravity acp --login`, needs no display. It prints a Google link and reads back the pasted code, with a callback on `https://antigravity.google/oauth-callback`. T3 does not use that route.

**Other findings.**

- The `t3code://pair?pairingUrl=` deep link did nothing in T3 Code (Nightly) 0.1.11. Pasting the pairing link into Settings, Environments, Add environment worked.
- A new machine has no git `user.name` or `user.email`, so T3 created a project without its first commit. A placeholder identity fixed it.
- `pkill -f 't3 serve'` also matches the sandbox's own start command, whose text contains the same words, and ends the sandbox with exit code 143. Stopping T3 must match only the `t3` user's processes.

## What changes in the plan

1. **Connect-token URLs replace public tunnels.** Each wake creates connect tokens for ports 3773 and 3774. The sandbox opens no `encrypted_ports`. Modal then turns away any request without the token, so the T3 environment and the daemon are no longer reachable by anyone who learns a URL. The proxy sends the token as a query parameter, because T3 needs the `Authorization` header for its own bearer. §5, §6.2, §9.2, §10 and §15.1 change. Still to check: how long a connect token lasts, and whether one sandbox can hold two tokens for different ports.
2. **Every machine runs on `runtime="vm"`.** `sudo`, Docker and cgroup CPU stats work only on it, Modal recommends it for memory snapshots, and disk snapshots work on it.
3. **Wakes are fast.** A fresh boot on the VM runtime took 4.2 to 11.0 s, 5.1 s typical, and a memory restore 1.7 s. The proxy's 90 s hold (§10.4) has plenty of margin.
4. **Rollover stays as planned.** Each wake gets a full timeout, so a machine that sleeps once a day never reaches the 24-hour limit.
5. **Sizing mode B.** Every size requests 0.25 cores and 1 GiB and sets its limit by size. Standby stays on at about $0.06 an hour.
6. **Docker moves from non-goals to supported.** It works on the VM runtime, which every machine now uses.
7. **Costs come from `app.sandboxes.stats` grouped by a machine tag.** It covers at most 7 days per call, so the controller sums it every hour into each machine's state.
8. **Memory sleep matters more than planned.** A fresh boot reads file contents at about 44 MB/s the first time, so the first build after a disk sleep is slow. Disk sleeps should stay rare.
9. **Sleeps after big installs can take minutes.** Snapshot time follows file count. The daemon should report "saving" in its status so the laptop can show it.
10. **Antigravity leaves v1.** The user dropped it after the Google sign-in failed on a machine. v1 ships with one provider, Claude Code. Antigravity with a Gemini API key was not tested and can come back later. §1, §2, §7, §11, §16, §17 and §19 change.
11. **Each machine gets a git identity when it is created.** Without one, T3 cannot make a project's first commit.
12. **Pairing shows a link to paste.** The `t3code://` deep link did nothing, so `t3modal` prints the pairing link and the user pastes it into T3 Code.

## Still to run

| # | Question | Why it waits |
|---|---|---|
| 4 | What snapshot storage costs | Modal publishes no price. Check the workspace's usage page after today's images expire |
| 11 | The controller's cold start | Measured when Phase 1 deploys the controller |
| 2 | Read speed after a memory restore, and on a second read after a fresh boot | Next small experiment |
| 7 | WebSockets idle for 10 minutes or more | Experiment 8 |
| 15 | Every call T3 Code makes on connect | Recorded in Phase 4 |
| 18 | How long a connect token lasts, and whether one sandbox holds tokens for two ports | Experiment 8 |
| 19 | What a connect URL answers once its sandbox has ended | Experiment 8 |
| 20 | Whether Claude's sign-in shows the paste-code step with nothing on its localhost port | Phase 2 |
| 21 | Whether Docker's data survives a memory sleep and a disk sleep | Phase 2 |
