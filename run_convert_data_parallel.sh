#!/usr/bin/env bash
set -u

INPUT_DIR="${1:-convert_data}"
OUTPUT_DIR="${2:-results}"
JOBS="${JOBS:-120}"
TIME_LIMIT="${TIME_LIMIT:-1200s}"
SOLVER="${SOLVER:-./nia_ls/build/nia_ls_main}"
SHARD_COUNT="${SHARD_COUNT:-1}"
SHARD_INDEX="${SHARD_INDEX:-0}"

if [[ ! -d "$INPUT_DIR" ]]; then
  echo "error: input directory not found: $INPUT_DIR" >&2
  exit 2
fi

if [[ ! -x "$SOLVER" ]]; then
  echo "error: solver not found or not executable: $SOLVER" >&2
  exit 2
fi

if [[ "$TIME_LIMIT" != "0" ]] && ! command -v timeout >/dev/null 2>&1; then
  echo "error: timeout command not found; set TIME_LIMIT=0 to disable it." >&2
  exit 2
fi

if (( SHARD_COUNT < 1 )); then
  echo "error: SHARD_COUNT must be >= 1" >&2
  exit 2
fi

if (( SHARD_INDEX < 0 || SHARD_INDEX >= SHARD_COUNT )); then
  echo "error: SHARD_INDEX must be in [0, SHARD_COUNT)" >&2
  exit 2
fi

mkdir -p "$OUTPUT_DIR"

run_one() {
  local f="$1"
  local rel out

  rel="${f#"$INPUT_DIR"/}"
  out="$OUTPUT_DIR/$rel"
  mkdir -p "$(dirname "$out")"

  echo "running: $rel"

  if [[ "$TIME_LIMIT" == "0" ]]; then
    "$SOLVER" "$f" >"$out" 2>/dev/null
  else
    timeout "$TIME_LIMIT" "$SOLVER" "$f" >"$out" 2>/dev/null
  fi

  local exit_code=$?

  if [[ "$exit_code" -eq 0 ]]; then
    echo "ok: $rel"
  else
    echo "failed: $rel (exit code $exit_code)" >&2
  fi

  return "$exit_code"
}

export INPUT_DIR OUTPUT_DIR TIME_LIMIT SOLVER
export -f run_one

find "$INPUT_DIR" -type f -name '*.txt' -print0 |
  sort -z |
  awk -v RS='\0' -v ORS='\0' -v shard_count="$SHARD_COUNT" -v shard_index="$SHARD_INDEX" '
    (NR - 1) % shard_count == shard_index { print }
  ' |
  xargs -0 -n 1 -P "$JOBS" bash -c 'run_one "$0"'
