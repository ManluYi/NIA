#!/bin/bash

INPUT_DIR=${1:-"/mnt/home/yimanlu/nia/data/benchmarks/QF_NIA"}
RESULT_BASE=${2:-"/mnt/home/yimanlu/nia/results"}
RESULT_DIR="$RESULT_BASE/optimathsat"
SOLVER_BASE="/mnt/home/yimanlu/nia/solvers"
SOLVER_EXE="$SOLVER_BASE/optimathsat-1.7.4-linux-64-bit/bin/optimathsat"
TIMEOUT_SECONDS=1200
HARD_TIMEOUT_SECONDS=$((TIMEOUT_SECONDS + 15))
CPU_LIMIT_SECONDS=$((HARD_TIMEOUT_SECONDS + 10))
SOLVER_CMD=(
    python3
    /mnt/home/yimanlu/nia/scripts/python/run_optimathsat_with_progress.py
    --solver-exe
    "$SOLVER_EXE"
    --timeout-seconds
    "$TIMEOUT_SECONDS"
    --kill-after-seconds
    5
    --
    -optimization=true
    -model_generation=true
    -opt.verbose=true
    -opt.soft_timeout=true
    -theory.la.enabled=true
    -theory.na.enabled=true
    -verbosity=0
    -opt.strategy=lin
)

if [ ! -x "$SOLVER_EXE" ]; then
    echo "Solver executable is not available: $SOLVER_EXE"
    exit 1
fi

SEND_THREAD_NUM=120
tmp_fifofile="/tmp/$$.fifo"
mkfifo "$tmp_fifofile"
exec 6<> "$tmp_fifofile"
rm "$tmp_fifofile"
for i in $(seq 1 $SEND_THREAD_NUM); do echo; done >&6

mkdir -p "$RESULT_DIR"

cleanup() {
    exec 6>&- 2>/dev/null || true
    jobs -pr | xargs -r kill 2>/dev/null || true
}

trap cleanup EXIT INT TERM

# Give the wrapper enough CPU budget to report timeout cleanly before the shell
# hits the hard CPU limit and prints a noisy "Killed" message.
ulimit -t $CPU_LIMIT_SECONDS
ulimit -v 16777216
ulimit -s unlimited

while read -r dir_file; do
    read -u 6
    {
        relative_path="${dir_file#$INPUT_DIR/}"
        result_file="$RESULT_DIR/${relative_path//\//_}.out"

        release_slot() {
            echo "=================" >> "$result_file"
            echo >&6
        }

        trap release_slot EXIT

        echo "Running: $dir_file"
        echo "=== $dir_file ===" > "$result_file"

        (
            time timeout --foreground --signal=SIGINT --kill-after=5s "${HARD_TIMEOUT_SECONDS}s" \
                stdbuf -oL -eL "${SOLVER_CMD[@]}" "$dir_file"
        ) >> "$result_file" 2>&1

        echo "[DONE] $dir_file -> $result_file"
    } &
done < <(find -L "$INPUT_DIR" -type f -name "*.smt2")

wait
exec 6>&- 2>/dev/null || true

if [ -f "../python/tongji_NIA.py" ]; then
    echo "Generating summary report..."
    python3 ../python/tongji_NIA.py "optimathsat" --input_dir "$INPUT_DIR" --result_dir "$RESULT_DIR"
fi

echo "Done. Results saved in $RESULT_DIR"
