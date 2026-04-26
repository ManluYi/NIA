#!/bin/bash


# ==================================================
# 针对 NIA 优化的精简版脚本 (OptiMathSAT 1.7.4)
# ==================================================

if [ $# -lt 1 ]; then
    echo "用法: $0 <solver_name> [input_dir] [result_dir]"
    exit 1
fi

SOLVER_NAME=$1
INPUT_DIR=${2:-"/mnt/home/yimanlu/nia/data/benchmarks/QF_NIA"}  # 默认输入目录
RESULT_BASE=${3:-"/mnt/home/yimanlu/nia/results"}
RESULT_DIR="$RESULT_BASE/$SOLVER_NAME"
SOLVER_BASE="/mnt/home/yimanlu/nia/solvers"

case $SOLVER_NAME in
    z3)
        SOLVER_EXE="$SOLVER_BASE/z3-master/build/z3"
        SOLVER_CMD="$SOLVER_EXE -v:10"
        ;;
    optimathsat)
        SOLVER_EXE="$SOLVER_BASE/optimathsat-1.7.4-linux-64-bit/bin/optimathsat"
        # 【精简配置】：
        # 1. -verbosity=0：关闭 preprocessing, push, searching 等过程信息
        # 2. -model_generation=true：输出中间模型/目标值结果
        # 3. -opt.verbose=true：打印每次更新后的优化信息
        SOLVER_CMD="$SOLVER_EXE -optimization=true -model_generation=true -opt.verbose=true -theory.la.enabled=true -theory.na.enabled=true -verbosity=0 -opt.strategy=lin"
        ;;
    *)
        echo "错误：未知求解器 $SOLVER_NAME"
        exit 1
        ;;
esac

if [ ! -x "$SOLVER_EXE" ]; then
    echo "错误：求解器不可用: $SOLVER_EXE"
    exit 1
fi

# 并行控制 (120 线程)
SEND_THREAD_NUM=120  
tmp_fifofile="/tmp/$$.fifo"
mkfifo "$tmp_fifofile"
exec 6<> "$tmp_fifofile"
rm "$tmp_fifofile"
for i in $(seq 1 $SEND_THREAD_NUM); do echo; done >&6

mkdir -p "$RESULT_DIR"

# 资源限制
ulimit -t 1200
ulimit -v 16777216     
ulimit -s unlimited    

# 运行循环
find -L "$INPUT_DIR" -type f -name "*.smt2" | while read -r dir_file; do
    read -u 6
    {
        relative_path="${dir_file#$INPUT_DIR/}"
        result_file="$RESULT_DIR/${relative_path//\//_}.out"

        echo "Running: $dir_file"
        echo "=== $dir_file ===" > "$result_file"
        
        # 执行求解
        ( time timeout --foreground --signal=SIGINT 1200s stdbuf -oL -eL $SOLVER_CMD "$dir_file" ) >> "$result_file" 2>&1
        
        echo "=================" >> "$result_file"
        echo "[DONE] $dir_file -> $result_file"
        echo >&6 
    } &
done

wait
exec 6>&-

# 统计报表
if [ -f "../python/tongji_NIA.py" ]; then
    echo "正在生成统计报表..."
    python3 ../python/tongji_NIA.py "$SOLVER_NAME" --input_dir "$INPUT_DIR" --result_dir "$RESULT_DIR"
fi

echo "任务完成！结果保存在 $RESULT_DIR"