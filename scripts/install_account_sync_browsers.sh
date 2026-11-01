#!/bin/sh
# Install engines needed by this suite without changing its test selection.
set -eu

case "${1:-}" in
  chromium)
    npm --prefix web exec -- playwright install --with-deps chromium
    ;;
  ipad-webkit|iphone-webkit)
    npm --prefix web exec -- playwright install --with-deps webkit
    ;;
  contracts)
    # Storage-upgrade and migration cover both engines.
    npm --prefix web exec -- playwright install --with-deps chromium webkit
    ;;
  *)
    echo "Unknown account-sync suite: ${1:-missing}" >&2
    exit 2
    ;;
esac
