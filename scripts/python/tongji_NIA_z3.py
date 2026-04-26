#!/usr/bin/env python3
import re
import sys
import argparse
from pathlib import Path

# 默认路径
DEFAULT_INPUT_DIR = "/mnt/home/yimanlu/nia/data/benchmarks/QF_NIA"
DEFAULT_RESULT_BASE = "/mnt/home/yimanlu/nia/results"
DEFAULT_TIMEOUT = 1200


def parse_time_line(line):
    line = line.strip()
    m = re.search(r"real\s+(\d+)m([\d.]+)s", line)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.search(r"real\s+([\d.]+)", line)
    if m:
        return float(m.group(1))
    return None


def parse_status(content_lines):
    for line in content_lines:
        line = line.strip().lower()
        if "no_model_found_within_local_search_budget" in line:
            return "no_model"
        if line in ("sat", "unsat", "unknown", "(sat)", "(unsat)", "(unknown)"):
            return line.strip("()")
    return "unknown"


def clean_smt_num(raw_str):
    if not raw_str:
        return None
    s = raw_str.replace(" ", "").replace("(-", "-")
    s = re.sub(r"[():>\s]+", "", s)
    s = s.strip()
    if "unbounded" in s:
        return "unbounded"
    if "oo" in s:
        return "-oo" if "-" in s else "oo"
    if re.match(r"^-?\d+(\.\d+)?$", s):
        return s
    return None


def parse_objective(lines):
    full_text = "\n".join(lines)

    if "(objectives" in full_text:
        start_idx = full_text.find("(objectives")
        matches = re.findall(r"\([^\s]+\s+([-?\d\s.()oobunced]+)\)", full_text[start_idx:])
        if matches:
            for m in reversed(matches):
                val = clean_smt_num(m)
                if val is not None:
                    return val

    objective_line_patterns = [
        re.compile(r"^objective\([^)]+\)\s*=\s*(.+)$", re.IGNORECASE),
        re.compile(r"(?:cost|bound|objective|value|obj)\s*[:=]\s*([-?\d\s.()a-zA-Z]+)", re.IGNORECASE),
        re.compile(r"->\s*([-?\d\s.()a-zA-Z]+)"),
    ]

    for line in reversed(lines):
        stripped = line.strip()
        for pat in objective_line_patterns:
            m = pat.search(stripped)
            if m:
                val = clean_smt_num(m.group(1))
                if val is not None:
                    return val
    return None


def process_result_file(filepath, input_dir, timeout):
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return None

    file_path = None
    for line in lines:
        if line.startswith("=== "):
            m = re.match(r"=== (.*) ===", line)
            if m:
                file_path = m.group(1).strip()
                break
    if not file_path:
        file_path = str(filepath)

    status = parse_status(lines)
    objective_val = parse_objective(lines)

    time_sec = 0.0
    for line in reversed(lines):
        t = parse_time_line(line)
        if t is not None:
            time_sec = t
            break

    if status == "unknown" and (time_sec > (timeout - 10) or time_sec == 0.0):
        time_sec = float(timeout)

    try:
        example = str(Path(file_path).resolve().relative_to(Path(input_dir).resolve())).replace("/", "_")
    except Exception:
        example = Path(file_path).name
    example = example.replace("?", "_")

    return (example, status, time_sec, objective_val)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("solver_name")
    parser.add_argument("--out", "-o")
    parser.add_argument("--input_dir", default=DEFAULT_INPUT_DIR)
    parser.add_argument("--result_dir")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    res_dir = Path(args.result_dir) if args.result_dir else Path(DEFAULT_RESULT_BASE) / args.solver_name
    if not res_dir.exists():
        print(f"错误: 目录不存在 {res_dir}")
        sys.exit(1)

    all_data = []
    for f in res_dir.rglob("*.out"):
        res = process_result_file(f, args.input_dir, args.timeout)
        if res:
            all_data.append(res)

    all_data.sort()

    output_path = args.out if args.out else f"{args.solver_name}_results.csv"
    out_f = open(output_path, "w", encoding="utf-8", newline="")
    try:
        out_f.write("Solver,Example,Status,Time(s),ObjectiveValue\n")
        for item in all_data:
            obj_str = item[3] if item[3] is not None else ""
            out_f.write(f"{args.solver_name},{item[0]},{item[1]},{item[2]:.3f},{obj_str}\n")
    finally:
        out_f.close()

    print(f"CSV written to: {output_path}")


if __name__ == "__main__":
    main()

