#!/usr/bin/env bash
set -u

RESULT_DIR="${1:-results}"
SUMMARY_FILE="${2:-"$RESULT_DIR/summary.tsv"}"

if [[ ! -d "$RESULT_DIR" ]]; then
  echo "error: result directory not found: $RESULT_DIR" >&2
  exit 2
fi

mkdir -p "$(dirname "$SUMMARY_FILE")"
printf "file\tstatus\tobjective_name\tobjective_value\n" > "$SUMMARY_FILE"

total=0
sat=0
unsat=0
no_model=0
timeout_or_incomplete=0
objective_count=0
other=0

while IFS= read -r -d '' file; do
  total=$((total + 1))
  rel="${file#"$RESULT_DIR"/}"

  objective_line="$(grep -m 1 '^objective(' "$file" || true)"
  objective_name=""
  objective_value=""
  if [[ -n "$objective_line" ]]; then
    objective_name="${objective_line#objective(}"
    objective_name="${objective_name%%)*}"
    objective_value="${objective_line##*= }"
    objective_count=$((objective_count + 1))
  fi

  if grep -q '^sat$' "$file"; then
    status="sat"
    sat=$((sat + 1))
  elif grep -q '^unsat' "$file"; then
    status="unsat"
    unsat=$((unsat + 1))
  elif grep -q '^no_model_found_within_local_search_budget$' "$file"; then
    status="no_model"
    no_model=$((no_model + 1))
  elif [[ ! -s "$file" ]] || ! grep -qE '^(sat|unsat|no_model_found_within_local_search_budget)$' "$file"; then
    status="timeout_or_incomplete"
    timeout_or_incomplete=$((timeout_or_incomplete + 1))
  else
    status="other"
    other=$((other + 1))
  fi

  printf "%s\t%s\t%s\t%s\n" "$rel" "$status" "$objective_name" "$objective_value" >> "$SUMMARY_FILE"
done < <(find "$RESULT_DIR" -type f -name '*.txt' ! -name "$(basename "$SUMMARY_FILE")" -print0 | sort -z)

echo "results: $RESULT_DIR"
echo "summary: $SUMMARY_FILE"
echo "total: $total"
echo "sat: $sat"
echo "unsat: $unsat"
echo "no_model: $no_model"
echo "timeout_or_incomplete: $timeout_or_incomplete"
echo "other: $other"
echo "objective_found: $objective_count"

