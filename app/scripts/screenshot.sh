#!/bin/zsh
# Relaunches build/Relay.app on a screen and saves window screenshots.
#   scripts/screenshot.sh <now|suggestions|quotas|report|settings> <out-dir> [wait-seconds]
# The menu-bar popover content is saved as popover.png. Needs Screen Recording permission for the
# terminal that runs it.
set -euo pipefail
cd "${0:A:h}/.."
screen=$1 out=$2 wait=${3:-4}
mkdir -p "$out"
pkill -x Relay 2>/dev/null || true
sleep 1
open build/Relay.app --args --show "$screen" --popover
sleep "$wait"
swift scripts/windows.swift | while read -r id layer title; do
  case "$title" in
    "relay") screencapture -x -o -l "$id" "$out/$screen.png" ;;
    "relay menu") screencapture -x -o -l "$id" "$out/popover.png" ;;
  esac
done
ls "$out"
