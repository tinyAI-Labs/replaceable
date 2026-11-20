#!/bin/sh
# run every example from a clean directory and compare its VERDICT line with its expected.txt (needs python3 and lean on PATH)
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
scripts="$root/skills/replaceable/scripts"
status=0
for dir in "$root"/examples/*/; do
  work=$(mktemp -d)
  cp "$dir/raw.jsonl" "$work"
  got=$(cd "$work" && python3 "$scripts/prepare.py" raw.jsonl > /dev/null && python3 "$scripts/run.py" --why | grep '^VERDICT:')
  if [ "$got" = "$(cat "$dir/expected.txt")" ]; then echo "ok    $(basename "$dir")"; else echo "FAIL  $(basename "$dir"): $got"; status=1; fi
  rm -rf "$work"
done
exit $status
