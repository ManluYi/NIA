#!/usr/bin/env python3
"""Summarize Z3 result files into a TSV."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


HEADER_RE = re.compile(r"^===\s+(.*?)\s+===$", re.MULTILINE)
STATUS_RE = re.compile(r"^(sat|unsat|unknown)\s*$", re.MULTILINE)
OBJ_START_RE = re.compile(r"^# obj\((.+?)\) := ", re.MULTILINE)
OBJECTIVE_ENTRY_LINE_RE = re.compile(r"^\(\s*([^()\s]+)\s+(.+)\)\s*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", nargs="?", default="results/z3")
    parser.add_argument("output_tsv", nargs="?", default="results/z3_summary.tsv")
    parser.add_argument("--input-dir", default="")
    return parser.parse_args()


def normalize_file_field(source_file: str, input_dir: str) -> str:
    if not input_dir:
        return source_file

    input_prefix = input_dir.rstrip("/")
    if source_file.startswith(input_prefix + "/"):
        return source_file[len(input_prefix) + 1 :]
    return source_file


def extract_first_objective(text: str) -> tuple[str, str]:
    in_objectives_block = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "(objectives":
            in_objectives_block = True
            continue

        if not in_objectives_block:
            continue

        if line == ")":
            break

        match = OBJECTIVE_ENTRY_LINE_RE.match(line)
        if match:
            return match.group(1), match.group(2).strip()

    return "", ""


def parse_result_file(path: Path, input_dir: str) -> tuple[str, str, str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")

    header_match = HEADER_RE.search(text)
    source_file = header_match.group(1) if header_match else str(path)
    file_field = normalize_file_field(source_file, input_dir)

    status_match = STATUS_RE.search(text)
    status = status_match.group(1) if status_match else "unknown"

    objective_name, objective_value = extract_first_objective(text)

    if not objective_name:
        obj_match = OBJ_START_RE.search(text)
        if obj_match:
            objective_name = obj_match.group(1)

    return file_field, status, objective_name, objective_value


def main() -> int:
    args = parse_args()
    result_dir = Path(args.result_dir)
    output_tsv = Path(args.output_tsv)

    if not result_dir.is_dir():
        raise SystemExit(f"result directory not found: {result_dir}")

    output_tsv.parent.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, str, str, str]] = []
    for path in sorted(result_dir.rglob("*.out")):
        rows.append(parse_result_file(path, args.input_dir))

    with output_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["file", "status", "objective_name", "objective_value"])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_tsv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
