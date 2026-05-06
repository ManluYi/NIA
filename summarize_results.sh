#!/usr/bin/env bash
set -u

RESULT_DIR="${1:-results}"
SUMMARY_FILE="${2:-"$RESULT_DIR/summary.tsv"}"
ALIGNED_FILE="${SUMMARY_FILE%.tsv}_aligned.txt"
INPUT_DIR="${INPUT_DIR:-}"
STRIP_SUFFIX="${STRIP_SUFFIX:-.txt}"

if [[ ! -d "$RESULT_DIR" ]]; then
  echo "error: result directory not found: $RESULT_DIR" >&2
  exit 2
fi

mkdir -p "$(dirname "$SUMMARY_FILE")"
printf "file\tstatus\tobjective_name\tobjective_value\n" > "$SUMMARY_FILE"

total=0
sat=0
unsat=0
unknown=0
no_model=0
timeout_or_incomplete=0
parse_error=0
objective_found=0

normalize_file_field() {
  local source_file="$1"
  local normalized="$source_file"

  if [[ -n "$INPUT_DIR" ]]; then
    local trimmed_input="${INPUT_DIR%/}"
    local prefix_forward
    local prefix_back
    trimmed_input="${trimmed_input%\\}"
    prefix_forward="$trimmed_input/"
    prefix_back="${trimmed_input}\\"
    if [[ "$normalized" == "$prefix_forward"* ]]; then
      normalized="${normalized#"$prefix_forward"}"
    elif [[ "$normalized" == "$prefix_back"* ]]; then
      normalized="${normalized#"$prefix_back"}"
    fi
  fi

  normalized="${normalized//\\//}"
  if [[ -n "$STRIP_SUFFIX" && "$normalized" == *"$STRIP_SUFFIX" ]]; then
    normalized="${normalized%"$STRIP_SUFFIX"}"
  fi

  printf '%s\n' "$normalized"
}

while IFS= read -r -d '' file; do
  total=$((total + 1))
  rel="${file#"$RESULT_DIR"/}"

  header_line="$(grep -m 1 '^=== .* ===$' "$file" || true)"
  source_line="$(grep -m 1 '^source: ' "$file" || true)"
  if [[ -n "$header_line" ]]; then
    source_file="${header_line#=== }"
    source_file="${source_file% ===}"
  elif [[ -n "$source_line" ]]; then
    source_file="${source_line#source: }"
  else
    source_file="$rel"
  fi
  file_field="$(normalize_file_field "$source_file")"

  objective_line="$(grep -m 1 '^objective(' "$file" || true)"
  objective_name=""
  objective_value=""
  if [[ -n "$objective_line" ]]; then
    objective_name="${objective_line#objective(}"
    objective_name="${objective_name%%)*}"
    objective_value="${objective_line##*= }"
    objective_found=$((objective_found + 1))
  fi

  if grep -q '^sat$' "$file"; then
    status="sat"
    sat=$((sat + 1))
  elif grep -q '^unsat' "$file"; then
    status="unsat"
    unsat=$((unsat + 1))
  elif grep -q '^no_model_found_within_local_search_budget$' "$file"; then
    status="unknown"
    unknown=$((unknown + 1))
    no_model=$((no_model + 1))
  elif [[ ! -s "$file" ]] || ! grep -qE '^(sat|unsat|no_model_found_within_local_search_budget)$' "$file"; then
    status="unknown"
    unknown=$((unknown + 1))
    timeout_or_incomplete=$((timeout_or_incomplete + 1))
  else
    status="unknown"
    unknown=$((unknown + 1))
    parse_error=$((parse_error + 1))
  fi

  printf "%s\t%s\t%s\t%s\n" "$file_field" "$status" "$objective_name" "$objective_value" >> "$SUMMARY_FILE"
done < <(find "$RESULT_DIR" -type f -name '*.txt' ! -name "$(basename "$SUMMARY_FILE")" -print0 | sort -z)

echo "results: $RESULT_DIR"
echo "summary: $SUMMARY_FILE"
awk -F '\t' '
  {
    rows[NR] = $0
    for (i = 1; i <= NF; i++) {
      cell[NR, i] = $i
      if (length($i) > width[i]) {
        width[i] = length($i)
      }
    }
    if (NF > max_nf) {
      max_nf = NF
    }
  }
  END {
    for (r = 1; r <= NR; r++) {
      for (i = 1; i <= max_nf; i++) {
        value = cell[r, i]
        if (i < max_nf) {
          printf "%-*s  ", width[i], value
        } else {
          printf "%s", value
        }
      }
      printf "\n"
    }
  }
' "$SUMMARY_FILE" > "$ALIGNED_FILE"
echo "aligned: $ALIGNED_FILE"
echo "total: $total"
echo "sat: $sat"
echo "unsat: $unsat"
echo "unknown: $unknown"
echo "no_model: $no_model"
echo "timeout_or_incomplete: $timeout_or_incomplete"
echo "parse_error: $parse_error"
echo "objective_found: $objective_found"