#!/bin/sh
# The machine's supervisor (implementation.md §7.2). It runs under tini as the sandbox's first
# process and never exits, because the sandbox ends without a snapshot when its first process exits.
#
# It supervises three loops:
# - T3 as the user t3 on port 3773. T3 needs both --host and --port: without --port it picks the
#   first free port from 3773, and without --host it doesn't listen on the sandbox's interface.
# - The daemon as t3 on port 3774, from ~/.t3modal/daemon.mjs once provisioning installs one, or
#   /opt/t3modal/daemon.mjs before. It inherits this script's environment, which holds the
#   machine's T3MODAL_ variables and MODAL_SANDBOX_ID. T3 starts with an empty environment, so its
#   agents don't get the machine secret by accident. They run as t3 with sudo, so this is no wall.
# - dockerd as root. T3 doesn't wait for it.
# Each loop records its process's PID in ~/.t3modal/<name>.pid, so the daemon can stop T3 by PID
# (§7.2), and leaves the process stopped while ~/.t3modal/<name>.hold exists. A process that keeps
# crashing restarts with backoff up to 60 s. A stop under a hold is deliberate, so it resets the
# backoff and logs no crash.
set -u

T3_USER=t3
T3_HOME=/home/t3
STATE="$T3_HOME/.t3modal"
LOGS=/var/log/t3modal

mkdir -p "$STATE" "$LOGS"
chown "$T3_USER:$T3_USER" "$STATE"

# run_as <how> <command...>: replaces this shell with the command. how is root, t3 for t3 with an
# empty environment, or t3+env for t3 with this script's environment. Secrets never go in argv.
run_as() {
  how=$1
  shift
  case $how in
    root) exec "$@" ;;
    t3) reset=--reset-env ;;
    t3+env) reset= ;;
    *) echo "run_as: unknown $how" >&2; exit 1 ;;
  esac
  # $reset is unquoted on purpose, so an empty value adds no argument.
  exec setpriv --reuid="$T3_USER" --regid="$T3_USER" --init-groups $reset \
    env HOME="$T3_HOME" USER="$T3_USER" LOGNAME="$T3_USER" SHELL=/bin/bash \
    PATH="$T3_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" "$@"
}

# supervise <name> <how> <command...>: runs the command, as run_as describes, until the sandbox ends.
supervise() {
  name=$1
  how=$2
  shift 2
  delay=1
  while true; do
    if [ -e "$STATE/$name.hold" ]; then
      sleep 2
      continue
    fi
    started=$(date +%s)
    (cd "$T3_HOME" && run_as "$how" "$@") >>"$LOGS/$name.log" 2>&1 &
    pid=$!
    echo "$pid" >"$STATE/$name.pid"
    wait "$pid"
    code=$?
    rm -f "$STATE/$name.pid"
    if [ -e "$STATE/$name.hold" ]; then
      echo "$(date -u +%FT%TZ) $name stopped with $code under a hold" >>"$LOGS/supervisor.log"
      delay=1
      continue
    fi
    if [ $(($(date +%s) - started)) -ge 60 ]; then
      delay=1
    else
      delay=$((delay * 2 > 60 ? 60 : delay * 2))
    fi
    echo "$(date -u +%FT%TZ) $name exited with $code, restarting in ${delay}s" >>"$LOGS/supervisor.log"
    sleep "$delay"
  done
}

supervise t3 t3 t3 serve --host 0.0.0.0 --port 3773 &
# The sh picks the daemon file at each start, so a provisioned daemon replaces the image's one at
# its next restart. It execs node, so the recorded PID is node's.
supervise daemon t3+env sh -c 'f="$HOME/.t3modal/daemon.mjs"; [ -e "$f" ] || f=/opt/t3modal/daemon.mjs; exec node "$f"' &
supervise dockerd root dockerd &

while true; do
  wait
  sleep 3600
done
