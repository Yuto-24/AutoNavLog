#!/bin/sh
# Each invocation runs inside its own Firestore emulator and runner.
set -eu

case "${1:-}" in
  chromium|ipad-webkit|iphone-webkit)
    npm --prefix web run test:sync -- --project="$1" --workers=2
    ;;
  contracts)
    npm --prefix web run test:firestore
    npm --prefix web run test:storage-upgrade
    npm --prefix web run test:migration
    ;;
  *)
    echo "Unknown account-sync suite: ${1:-missing}" >&2
    exit 2
    ;;
esac
