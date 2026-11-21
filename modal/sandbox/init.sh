#!/bin/sh
# The machine's supervisor (implementation.md §7.2). It runs under tini as the sandbox's first
# process and never exits, because the sandbox ends without a snapshot when its first process exits.
#
# It runs T3 as the user t3 on port 3773. T3 needs both --host and --port: without --port it picks
# the first free port from 3773, and without --host it doesn't listen on the sandbox's interface.
# The loop records T3's own PID, so the daemon can stop T3 by PID (§7.2), and it leaves T3 stopped
# while ~/.t3modal/t3.hold exists. A process that keeps crashing restarts with backoff up to 60 s.
# A stop under a hold is deliberate, so it resets the backoff and logs no crash.
# The daemon and dockerd join in #9.
set -u

T3_USER=t3
T3_HOME=/home/t3
STATE="$T3_HOME/.t3modal"
LOGS=/var/log/t3modal

mkdir -p "$STATE" "$LOGS"
chown "$T3_USER:$T3_USER" "$STATE"

as_t3() {
  exec setpriv --reuid="$T3_USER" --regid="$T3_USER" --init-groups --reset-env \
    env HOME="$T3_HOME" USER="$T3_USER" LOGNAME="$T3_USER" SHELL=/bin/bash \
    PATH="$T3_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" "$@"
}

# supervise <name> <command...>: runs the command as t3 until the sandbox ends.
supervise() {
  name=$1
  shift
  delay=1
  while true; do
    if [ -e "$STATE/$name.hold" ]; then
      sleep 2
      continue
    fi
    started=$(date +%s)
    (cd "$T3_HOME" && as_t3 "$@") >>"$LOGS/$name.log" 2>&1 &
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

supervise t3 t3 serve --host 0.0.0.0 --port 3773 &

while true; do
  wait
  sleep 3600
done
