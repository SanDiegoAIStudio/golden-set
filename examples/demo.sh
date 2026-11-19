#!/bin/sh
# Offline tour. Writes only in a temporary folder.
set -u

TMP=""
cleanup() {
  if [ -n "$TMP" ]; then
    rm -rf "$TMP"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

HERE=$(dirname "$0")
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd) || exit 1
TMP=$(mktemp -d) || exit 1

cp "$ROOT/golden.py" "$TMP/golden.py" || exit 1
cp "$ROOT/agreement.py" "$TMP/agreement.py" || exit 1
mkdir -p "$TMP/examples" || exit 1
cp "$ROOT/examples/keyword_grader.py" "$TMP/examples/keyword_grader.py" || exit 1
cp "$ROOT/examples/set_cell.py" "$TMP/examples/set_cell.py" || exit 1
cp "$ROOT/examples/golden.csv" "$TMP/golden.csv" || exit 1
cd "$TMP" || exit 1

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
PY="${PYTHON:-python3}"

expect() {
  label=$1
  want_status=$2
  shift 2
  printf '$'
  for word in "$@"; do
    case "$word" in
      *" "*)
        printf ' "%s"' "$word"
        ;;
      *)
        printf ' %s' "$word"
        ;;
    esac
  done
  printf '\n'
  "$@"
  got_status=$?
  if [ "$got_status" -ne "$want_status" ]; then
    printf 'step differed: %s (expected exit %s, got %s)\n' "$label" "$want_status" "$got_status" >&2
    exit 1
  fi
}

expect lock 0 "$PY" golden.py lock golden.csv --bar 27
expect run 0 "$PY" golden.py run golden.csv -- "$PY" examples/keyword_grader.py
expect score-pass 0 "$PY" golden.py score golden.csv

expect edit-answer 0 "$PY" examples/set_cell.py golden.csv 12 mine urgent
expect score-refuse 2 "$PY" golden.py score golden.csv

expect edit-note 0 "$PY" examples/set_cell.py golden.csv 12 note "2026-01-15 the refund policy changed"
expect score-corrected 0 "$PY" golden.py score golden.csv

exit 0
