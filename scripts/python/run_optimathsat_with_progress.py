#!/usr/bin/env python3
"""Run OptiMathSAT, stream progress, and emit a final best-known result on timeout."""

from __future__ import annotations

import argparse
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field


OBJ_START_RE = re.compile(r"^# obj\((.+)\) := ")
OBJ_NEW_RE = re.compile(r"^# obj\((.+)\) -  new: (.+)$")
SEARCH_END_RE = re.compile(r"^# obj\((.+)\) - search end: (.+)$")
STATUS_RE = re.compile(r"^(sat|unsat|unknown)\s*$")
ERROR_RE = re.compile(r'^\(error ".*"\)$')
OBJECTIVE_UNKNOWN_RE = re.compile(r"^\s*\(([^()\s]+)\s+unknown\),\s+range:")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver-exe", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=0)
    parser.add_argument("--kill-after-seconds", type=int, default=5)
    parser.add_argument("solver_args", nargs=argparse.REMAINDER)
    return parser.parse_args()


def emit(*args, **kwargs) -> None:
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


@dataclass
class RunState:
    last_values: dict[str, str] = field(default_factory=dict)
    saw_search_end: bool = False
    saw_status: bool = False
    last_status: str | None = None
    emitted_objectives_block: bool = False
    objective_unknown_names: set[str] = field(default_factory=set)

    def handle_line(self, line: str) -> None:
        match = OBJ_START_RE.match(line)
        if match:
            self.last_values.setdefault(match.group(1), "")
            return

        match = OBJ_NEW_RE.match(line)
        if match:
            self.last_values[match.group(1)] = match.group(2)
            return

        if SEARCH_END_RE.match(line):
            self.saw_search_end = True
            return

        if STATUS_RE.match(line):
            self.saw_status = True
            self.last_status = line.strip()
            return

        match = OBJECTIVE_UNKNOWN_RE.match(line)
        if match:
            self.objective_unknown_names.add(match.group(1))

    def best_known_values(self) -> list[tuple[str, str]]:
        return [(name, value) for name, value in self.last_values.items() if value]


def should_skip_line(line: str) -> bool:
    stripped = line.strip()
    return stripped == "unsupported" or ERROR_RE.match(stripped) is not None


def reader_thread(stream, output_queue: "queue.Queue[str | None]") -> None:
    try:
        for line in iter(stream.readline, ""):
            output_queue.put(line)
    finally:
        output_queue.put(None)


def should_emit_objectives_block(lines: list[str]) -> bool:
    stripped_lines = [line.rstrip("\n") for line in lines]
    if any(OBJECTIVE_UNKNOWN_RE.match(line) for line in stripped_lines):
        return False

    if any("partial search" in line for line in stripped_lines):
        return False

    return True


def emit_objectives_block(lines: list[str], state: RunState) -> None:
    if not lines:
        return

    if not should_emit_objectives_block(lines):
        return

    for line in lines:
        emit(line, end="")
    state.emitted_objectives_block = True


def print_objectives(values: list[tuple[str, str]]) -> None:
    if not values:
        return

    emit("(objectives")
    for name, value in values:
        emit(f" ({name} {value})")
    emit(")")


def terminate_process(proc: subprocess.Popen[str], kill_after_seconds: int) -> None:
    try:
        os.killpg(proc.pid, signal.SIGINT)
    except ProcessLookupError:
        return

    try:
        proc.wait(timeout=kill_after_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        proc.wait()


def main() -> int:
    args = parse_args()
    solver_args = list(args.solver_args)
    if solver_args and solver_args[0] == "--":
        solver_args = solver_args[1:]

    cmd = [args.solver_exe, *solver_args]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
    )

    assert proc.stdout is not None
    output_queue: "queue.Queue[str | None]" = queue.Queue()
    thread = threading.Thread(target=reader_thread, args=(proc.stdout, output_queue), daemon=True)
    thread.start()

    state = RunState()
    timed_out = False
    reader_done = False
    start_time = time.monotonic()
    buffered_objectives: list[str] = []
    in_objectives_block = False

    try:
        while True:
            try:
                line = output_queue.get(timeout=0.1)
                if line is None:
                    reader_done = True
                else:
                    stripped = line.rstrip("\n")
                    state.handle_line(stripped)
                    if in_objectives_block:
                        buffered_objectives.append(line)
                        if stripped == ")":
                            emit_objectives_block(buffered_objectives, state)
                            buffered_objectives = []
                            in_objectives_block = False
                        continue

                    if stripped == "(objectives":
                        buffered_objectives = [line]
                        in_objectives_block = True
                        continue

                    if not should_skip_line(stripped):
                        emit(line, end="")
            except queue.Empty:
                pass

            if proc.poll() is not None and reader_done:
                break

            if args.timeout_seconds > 0 and not timed_out and proc.poll() is None:
                if time.monotonic() - start_time >= args.timeout_seconds:
                    timed_out = True
                    terminate_process(proc, args.kill_after_seconds)
    except KeyboardInterrupt:
        timed_out = True
        terminate_process(proc, args.kill_after_seconds)

    while True:
        try:
            line = output_queue.get_nowait()
        except queue.Empty:
            break
        if line is None:
            continue
        stripped = line.rstrip("\n")
        state.handle_line(stripped)
        if in_objectives_block:
            buffered_objectives.append(line)
            if stripped == ")":
                emit_objectives_block(buffered_objectives, state)
                buffered_objectives = []
                in_objectives_block = False
            continue

        if stripped == "(objectives":
            buffered_objectives = [line]
            in_objectives_block = True
            continue

        if not should_skip_line(stripped):
            emit(line, end="")

    if buffered_objectives:
        emit_objectives_block(buffered_objectives, state)

    returncode = proc.wait()

    if (
        (timed_out or state.objective_unknown_names or not state.saw_status)
        and not state.saw_search_end
    ):
        names = list(state.objective_unknown_names) if state.objective_unknown_names else list(state.last_values)
        for name in names:
            emit(f"# obj({name}) - search end: unknown")

    if not state.saw_status:
        emit("unknown")
        values = state.best_known_values()
        if values:
            emit()
            print_objectives(values)
        return 1

    if (
        state.last_status == "unknown"
        and not state.emitted_objectives_block
        and not timed_out
    ):
        values = state.best_known_values()
        if values:
            emit()
            print_objectives(values)
            return 1

    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
