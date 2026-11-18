# t3modal

t3modal runs cloud machines for T3 Code on Modal. A machine sleeps when no one uses it and wakes when someone does, so it costs money only while it works.

## Language

### Machines

**Machine**:
A long-lived cloud computer that the user names, pairs with T3 Code, and keeps for weeks or months. Over its life it runs as many sandboxes, one at a time.
_Avoid_: Box, VM, instance, environment

**Sandbox**:
One Modal Sandbox run of a machine, from a wake to the next sleep. It ends at every sleep, and the next wake starts a new one.
_Avoid_: Container, machine (for a single run)

**T3 environment**:
The T3 server that runs on a machine and that T3 Code pairs with. A machine hosts exactly one.
_Avoid_: Environment (alone), server

### Power

**Sleep**:
Saving a machine and ending its sandbox.
_Avoid_: Stop, pause, shutdown, terminate (for the whole act)

**Memory sleep**:
A sleep that keeps the machine's running processes as well as its disk.
_Avoid_: Pause, suspend

**Disk sleep**:
A sleep that keeps only the machine's disk. The next wake is a fresh boot.
_Avoid_: Cold sleep, full stop

**Wake**:
Starting a new sandbox for a sleeping machine.
_Avoid_: Resume, start, restore (alone)

**Memory restore**:
A wake that continues the processes kept by a memory sleep.
_Avoid_: Resume

**Fresh boot**:
A wake that starts from the saved disk alone, with no processes running.
_Avoid_: Cold start, restore

**Standby**:
An awake machine that has been idle for a while and holds only a small amount of CPU and memory until it sleeps or someone uses it.
_Avoid_: Idle stage, nap, doze

**Rollover**:
A sleep and immediate wake that t3modal forces before a sandbox reaches Modal's 24-hour limit.
_Avoid_: Restart, recycle

**Checkpoint**:
A saved copy of a machine's disk taken while it keeps running.
_Avoid_: Backup, snapshot (alone)

### Parts of t3modal

**Controller**:
The t3modal app deployed in the user's own Modal workspace that creates, saves and ends sandboxes, and wakes machines on schedule.
_Avoid_: Backend, server, orchestrator

**Daemon**:
The t3modal process on each machine that decides when the machine sleeps and carries out requests from the laptop.
_Avoid_: Agent, watchdog

**Capture**:
What the laptop saved of a machine's T3 environment before it slept, used to answer T3 Code while the machine sleeps.
_Avoid_: Snapshot, cache, recording

### Agents

**Agent**:
A coding agent that T3 runs on a machine, such as Claude Code, OpenCode or Antigravity. Never t3modal's own processes.
_Avoid_: Assistant, bot

**Provider**:
T3's name for the kind of agent and the account it signs in with, such as Claude Code or Antigravity.
_Avoid_: Vendor, backend
