#!/bin/sh
# Runs the daemon's tests on Node 22.18 or later in the 22 line, the Node that machines run
# (implementation.md §7.1) and the first 22 release that strips TypeScript types by default.
# Uses the Node on PATH if it fits, otherwise fnm's Node 22, otherwise fails.
# Each test gets 10 s, so a request that never gets an answer fails instead of hanging.
# Passes its arguments to node --test, for example --test-name-pattern=foo.
cd "$(dirname "$0")/.." || exit 1
fits='const [a, b] = process.versions.node.split(".").map(Number); process.exit(a === 22 && b >= 18 ? 0 : 1)'
if node -e "$fits" 2>/dev/null; then
  exec node --test --test-timeout=10000 "$@" "src/**/*.test.ts"
fi
if command -v fnm >/dev/null 2>&1 && fnm exec --using=22 node -e "$fits" 2>/dev/null; then
  exec fnm exec --using=22 node --test --test-timeout=10000 "$@" "src/**/*.test.ts"
fi
echo "Daemon tests need Node 22.18 or later in the 22 line (see .node-version). Got $(node --version 2>/dev/null || echo none). Install it with: fnm install 22" >&2
exit 1
