#!/bin/sh
# PROTOTYPE. Stand-in for xdg-open on a machine with no desktop.
# T3 sets BROWSER to a shell-quoted command ending in '%s'. Debian's xdg-open
# runs it without a shell, so the quotes stay literal and the command fails.
# This runs BROWSER through a shell with the URL in place of %s.
[ -n "$BROWSER" ] || { echo "xdg-open: BROWSER is not set" >&2; exit 3; }
T3_URL="$1"
export T3_URL
eval "$(printf '%s\n' "$BROWSER" | sed "s/'%s'/\"\$T3_URL\"/; s/%s/\"\$T3_URL\"/")"
