#!/usr/bin/env bash
set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INPUT_DIR="${1:-$SCRIPT_DIR/convert_data}"
RESULT_BASE="${2:-$SCRIPT_DIR/results}"
RESULT_SUBDIR="${RESULT_SUBDIR:-nia_ls}"
JOBS="${JOBS:-120}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-1200}"
SOLVER="${SOLVER:-$SCRIPT_DIR/nia_ls/build/nia_ls_main}"
SHARD_COUNT="${SHARD_COUNT:-1}"
SHARD_INDEX="${SHARD_INDEX:-0}"
KILL_AFTER_SECONDS="${KILL_AFTER_SECONDS:-5}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SUMMARY_SCRIPT="${SUMMARY_SCRIPT:-$SCRIPT_DIR/scripts/python/summarize_nia_ls_results.py}"
COMPARE_SCRIPT="${COMPARE_SCRIPT:-$SCRIPT_DIR/scripts/python/compare_objective_summaries.py}"

if [[ -n "${RESULT_DIR:-}" ]]; then
  OUTPUT_DIR="$RESULT_DIR"
elif [[ "$RESULT_BASE" == "$RESULT_SUBDIR" || "$RESULT_BASE" == */"$RESULT_SUBDIR" ]]; then
  OUTPUT_DIR="$RESULT_BASE"
else
  OUTPUT_DIR="$RESULT_BASE/$RESULT_SUBDIR"
fi

RESULT_PARENT_DIR="$(dirname "$OUTPUT_DIR")"
RESULT_NAME="$(basename "$OUTPUT_DIR")"
SUMMARY_FILE="${SUMMARY_FILE:-$RESULT_PARENT_DIR/${RESULT_NAME}_summary.tsv}"
COMPARISON_FILE="${COMPARISON_FILE:-$RESULT_PARENT_DIR/objective_comparison.tsv}"
Z3_SUMMARY_FILE="${Z3_SUMMARY_FILE:-$RESULT_PARENT_DIR/z3_summary.tsv}"
OPTIMATHSAT_SUMMARY_FILE="${OPTIMATHSAT_SUMMARY_FILE:-$RESULT_PARENT_DIR/optimathsat_summary.tsv}"

if [[ -n "${TIME_LIMIT:-}" ]]; then
  if [[ "$TIME_LIMIT" == "0" ]]; then
    TIMEOUT_SECONDS=0
  elif [[ "$TIME_LIMIT" =~ ^([0-9]+)s$ ]]; then
    TIMEOUT_SECONDS="${BASH_REMATCH[1]}"
  else
    echo "error: TIME_LIMIT must be 0 or end with 's', for example 1200s." >&2
    exit 2
  fi
fi

HARD_TIMEOUT_SECONDS="${HARD_TIMEOUT_SECONDS:-$((TIMEOUT_SECONDS + 15))}"
CPU_LIMIT_SECONDS="${CPU_LIMIT_SECONDS:-$((HARD_TIMEOUT_SECONDS + 10))}"

if [[ ! -d "$INPUT_DIR" ]]; then
  echo "error: input directory not found: $INPUT_DIR" >&2
  exit 2
fi

if [[ ! -x "$SOLVER" ]]; then
  echo "error: solver not found or not executable: $SOLVER" >&2
  exit 2
fi

if (( TIMEOUT_SECONDS > 0 )) && ! command -v timeout >/dev/null 2>&1; then
  echo "error: timeout command not found; set TIMEOUT_SECONDS=0 to disable it." >&2
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

echo "input_dir: $INPUT_DIR"
echo "output_dir: $OUTPUT_DIR"
echo "solver: $SOLVER"
echo "jobs: $JOBS"
echo "timeout_seconds: $TIMEOUT_SECONDS"
echo "hard_timeout_seconds: $HARD_TIMEOUT_SECONDS"
echo "cpu_limit_seconds: $CPU_LIMIT_SECONDS"
echo "shard: $((SHARD_INDEX + 1))/$SHARD_COUNT"

# Match the resource guardrails used by the Z3 and OptiMathSAT wrappers so
# objective values and runtime behavior are compared under the same budget.
if (( TIMEOUT_SECONDS > 0 )); then
  ulimit -t "$CPU_LIMIT_SECONDS"
fi
ulimit -v 16777216
ulimit -s unlimited

run_one() {
  local f="$1"
  local rel out

  rel="${f#"$INPUT_DIR"/}"
  out="$OUTPUT_DIR/$rel"
  mkdir -p "$(dirname "$out")"

  echo "running: $rel"
  printf "=== %s ===\n" "$f" >"$out"

  if (( TIMEOUT_SECONDS == 0 )); then
    stdbuf -oL -eL "$SOLVER" "$f" >>"$out" 2>&1
  else
    (
      time timeout --foreground --signal=SIGINT --kill-after="${KILL_AFTER_SECONDS}s" "${HARD_TIMEOUT_SECONDS}s" \
        stdbuf -oL -eL "$SOLVER" "$f"
    ) >>"$out" 2>&1
  fi

  local exit_code=$?

  if [[ "$exit_code" -eq 0 ]]; then
    echo "ok: $rel"
  else
    echo "failed: $rel (exit code $exit_code)" >&2
  fi

  return "$exit_code"
}

export INPUT_DIR OUTPUT_DIR TIMEOUT_SECONDS SOLVER HARD_TIMEOUT_SECONDS KILL_AFTER_SECONDS
export -f run_one

run_status=0

find -L "$INPUT_DIR" -type f -name '*.txt' -print0 |
  sort -z |
  awk -v RS='\0' -v ORS='\0' -v shard_count="$SHARD_COUNT" -v shard_index="$SHARD_INDEX" '
    (NR - 1) % shard_count == shard_index { print }
  ' |
  xargs -0 -n 1 -P "$JOBS" bash -c 'run_one "$1"' _ || run_status=$?

if [[ -f "$SUMMARY_SCRIPT" ]]; then
  echo "summarizing nia_ls results -> $SUMMARY_FILE"
  "$PYTHON_BIN" "$SUMMARY_SCRIPT" "$OUTPUT_DIR" "$SUMMARY_FILE" --input-dir "$INPUT_DIR" --strip-suffix .txt || run_status=$?
fi

if [[ -f "$COMPARE_SCRIPT" && -f "$SUMMARY_FILE" && -f "$Z3_SUMMARY_FILE" && -f "$OPTIMATHSAT_SUMMARY_FILE" ]]; then
  echo "building objective comparison -> $COMPARISON_FILE"
  "$PYTHON_BIN" "$COMPARE_SCRIPT" \
    --z3 "$Z3_SUMMARY_FILE" \
    --optimathsat "$OPTIMATHSAT_SUMMARY_FILE" \
    --nia-ls "$SUMMARY_FILE" \
    "$COMPARISON_FILE" || run_status=$?
fi

echo "nia_ls run finished. results: $OUTPUT_DIR"
if [[ -f "$SUMMARY_FILE" ]]; then
  echo "nia_ls summary: $SUMMARY_FILE"
fi
if [[ -f "$COMPARISON_FILE" ]]; then
  echo "comparison: $COMPARISON_FILE"
fi

exit "$run_status"
