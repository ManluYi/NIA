#!/usr/bin/env python3
"""Run Z3 optimize on an SMT2 file and preserve best-known progress on crashes/timeouts."""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import importlib
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence


OBJ_START_RE = re.compile(r"^# obj\((.+)\) := ")
OBJ_NEW_RE = re.compile(r"^# obj\((.+)\) -  new: (.+)$")
SEARCH_END_RE = re.compile(r"^# obj\((.+)\) - search end: (.+)$")
STATUS_RE = re.compile(r"^(sat|unsat|unknown)\s*$")
ERROR_RE = re.compile(r'^\(error ".*"\)$')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file")
    parser.add_argument("--z3-root", default="")
    parser.add_argument("--z3-exe", default="")
    parser.add_argument("--timeout-ms", type=int, default=0)
    parser.add_argument("--kill-after-seconds", type=int, default=5)
    parser.add_argument("--child-mode", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def emit(*args, **kwargs) -> None:
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


@dataclass
class ParentState:
    last_values: dict[str, str] = field(default_factory=dict)
    saw_search_end: bool = False
    saw_status: bool = False
    last_status: str | None = None
    emitted_objectives_block: bool = False

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
    if not lines:
        return False
    return lines[0].rstrip("\n") == "(objectives" and lines[-1].rstrip("\n") == ")"


def emit_objectives_block(lines: list[str], state: ParentState) -> None:
    if not should_emit_objectives_block(lines):
        return

    for line in lines:
        emit(line, end="")
    state.emitted_objectives_block = True


def print_objectives(values: Sequence[tuple[str, str]]) -> None:
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


def unknown_reason_for_exit(returncode: int, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if returncode < 0:
        return f"child exited with signal {-returncode}"
    if returncode > 0:
        return f"child exited with code {returncode}"
    return ""


def run_parent(args: argparse.Namespace) -> int:
    child_cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child-mode",
        "--z3-root",
        args.z3_root,
        "--z3-exe",
        args.z3_exe,
        "--timeout-ms",
        str(args.timeout_ms),
        "--kill-after-seconds",
        str(args.kill_after_seconds),
        args.input_file,
    ]

    proc = subprocess.Popen(
        child_cmd,
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

    state = ParentState()
    timed_out = False
    reader_done = False
    start_time = time.monotonic()
    buffered_objectives: list[str] = []
    in_objectives_block = False
    wall_timeout_seconds = (args.timeout_ms / 1000.0 + 5.0) if args.timeout_ms > 0 else 0.0

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

            if wall_timeout_seconds > 0 and not timed_out and proc.poll() is None:
                if time.monotonic() - start_time >= wall_timeout_seconds:
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
    values = state.best_known_values()
    reason = unknown_reason_for_exit(returncode, timed_out)

    if (
        not state.saw_search_end
        and state.last_values
        and (timed_out or not state.saw_status or returncode != 0)
    ):
        for name in state.last_values:
            emit(f"# obj({name}) - search end: unknown")

    if not state.saw_status:
        emit("unknown")
        if reason:
            emit(f"; reason-unknown: {reason}")
        if values:
            emit()
            print_objectives(values)
        return 1

    if state.last_status in {"sat", "unknown"} and not state.emitted_objectives_block:
        if values:
            emit()
            print_objectives(values)
        return 0 if state.last_status == "sat" else 1

    return returncode


def ensure_importlib_resources() -> None:
    try:
        import importlib_resources  # type: ignore
        if hasattr(importlib_resources, "files"):
            return
    except ModuleNotFoundError:
        importlib_resources = None  # type: ignore

    try:
        import importlib.resources as importlib_resources  # type: ignore
        if hasattr(importlib_resources, "files"):
            sys.modules["importlib_resources"] = importlib_resources
            return
    except ModuleNotFoundError:
        importlib_resources = None  # type: ignore

    shim = types.ModuleType("importlib_resources")

    def files(package: str) -> Path:
        module = importlib.import_module(package)
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            raise ModuleNotFoundError(package)
        return Path(module_file).resolve().parent

    @contextlib.contextmanager
    def as_file(path):
        yield Path(path)

    shim.files = files  # type: ignore[attr-defined]
    shim.as_file = as_file  # type: ignore[attr-defined]
    sys.modules["importlib_resources"] = shim


def append_sys_path(path: Path) -> None:
    if path.is_dir() and str(path) not in sys.path:
        sys.path.insert(0, str(path))


def preload_library(candidates: Sequence[Path]) -> None:
    for path in candidates:
        if path.is_file():
            try:
                ctypes.CDLL(str(path), mode=getattr(ctypes, "RTLD_GLOBAL", 0))
                return
            except OSError:
                continue


def ensure_z3_importable(z3_root: str, z3_exe: str) -> None:
    root = Path(z3_root) if z3_root else None
    exe = Path(z3_exe) if z3_exe else None

    python_candidates: List[Path] = []
    lib_candidates: List[Path] = []

    if root:
        python_candidates.extend(
            [
                root / "build" / "python",
                root / "src" / "api" / "python",
                root / "build",
            ]
        )
        lib_candidates.extend(
            [
                root / "build" / "libz3.so",
                root / "build" / "libz3.dylib",
                root / "build" / "libz3.dll",
            ]
        )

    if exe:
        exe_dir = exe.parent
        exe_parent = exe_dir.parent
        python_candidates.extend(
            [
                exe_dir / "python",
                exe_parent / "python",
                exe_parent / "build" / "python",
                exe_parent / "src" / "api" / "python",
            ]
        )
        lib_candidates.extend(
            [
                exe_dir / "libz3.so",
                exe_dir / "libz3.dylib",
                exe_dir / "libz3.dll",
                exe_parent / "libz3.so",
                exe_parent / "libz3.dylib",
                exe_parent / "libz3.dll",
            ]
        )

    for path in python_candidates:
        append_sys_path(path)

    preload_library(lib_candidates)


def load_z3(z3_root: str, z3_exe: str):
    ensure_z3_importable(z3_root, z3_exe)
    ensure_importlib_resources()
    import z3  # type: ignore

    return z3


def extract_objective_directions(path: Path) -> List[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    directions: List[str] = []
    depth = 0
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if ch == ";":
            while i < n and text[i] != "\n":
                i += 1
            continue

        if ch == '"':
            i += 1
            while i < n:
                if text[i] == '"' and text[i - 1] != "\\":
                    i += 1
                    break
                i += 1
            continue

        if ch == "|":
            i += 1
            while i < n and text[i] != "|":
                i += 1
            i += 1
            continue

        if ch == "(":
            parent_depth = depth
            depth += 1
            i += 1

            if parent_depth == 0:
                while i < n and text[i].isspace():
                    i += 1
                start = i
                while i < n and not text[i].isspace() and text[i] not in "()":
                    i += 1
                head = text[start:i]
                if head in {"minimize", "maximize"}:
                    directions.append(head)
            continue

        if ch == ")":
            depth = max(0, depth - 1)

        i += 1

    return directions


def format_value(value) -> str:
    return str(value)


def classify_z3_exception(exc: Exception) -> tuple[str, str]:
    message = str(exc)
    lowered = message.lower()

    if "out of memory" in lowered:
        return "out_of_memory", "out of memory"

    if "interrupted" in lowered or "canceled" in lowered or "cancelled" in lowered:
        return "interrupted", message

    if "timeout" in lowered:
        return "timeout", message

    return "error", message


class ProgressPrinter:
    def __init__(self, z3, objective_exprs: Sequence, directions: Sequence[str]):
        self.z3 = z3
        self.objective_exprs = list(objective_exprs)
        self.directions = list(directions)
        self.steps = [0 for _ in self.objective_exprs]
        self.last_values = [None for _ in self.objective_exprs]

        for expr in self.objective_exprs:
            name = str(expr)
            emit(f"# obj({name}) := {name}")
            emit(f"# obj({name}) - search start: [ (- oo), oo ]")

    def on_model(self, model) -> None:
        for index, expr in enumerate(self.objective_exprs):
            value = self.z3.simplify(model.eval(expr, model_completion=True))
            value_text = format_value(value)
            if self.last_values[index] == value_text:
                continue

            self.steps[index] += 1
            self.last_values[index] = value_text
            name = str(expr)
            direction = self.directions[index] if index < len(self.directions) else "minimize"

            emit(f"# obj({name}) - linear step: {self.steps[index]}")
            emit(f"# obj({name}) -  new: {value_text}")
            if direction == "maximize":
                emit(f"# obj({name}) -  update lower: [ {value_text}, oo ]")
            else:
                emit(f"# obj({name}) -  update upper: [ (- oo), {value_text} ]")

    def finish(self, status: str) -> None:
        for index, expr in enumerate(self.objective_exprs):
            name = str(expr)
            emit(f"# obj({name}) - search end: {status}")
            if self.last_values[index] is not None and status == "sat_optimal":
                value_text = self.last_values[index]
                direction = self.directions[index] if index < len(self.directions) else "minimize"
                if direction == "maximize":
                    emit(f"# obj({name}) -  update upper: [ {value_text}, {value_text} ]")
                else:
                    emit(f"# obj({name}) -  update lower: [ {value_text}, {value_text} ]")

    def best_known_values(self) -> list[tuple[str, str]]:
        values: list[tuple[str, str]] = []
        for index, expr in enumerate(self.objective_exprs):
            value_text = self.last_values[index]
            if value_text is not None:
                values.append((str(expr), value_text))
        return values


def print_final_result(z3, opt, objective_exprs: Sequence, result) -> int:
    if result == z3.sat:
        emit("sat")
        model = opt.model()
        values = []
        for expr in objective_exprs:
            value = z3.simplify(model.eval(expr, model_completion=True))
            values.append((str(expr), str(value)))
        print_objectives(values)
        return 0

    if result == z3.unsat:
        emit("unsat")
        return 20

    emit("unknown")
    reason = opt.reason_unknown()
    if reason:
        emit(f"; reason-unknown: {reason}")
    return 1


def print_unknown_result(reason: str, printer: ProgressPrinter) -> int:
    emit("unknown")
    if reason:
        emit(f"; reason-unknown: {reason}")
    values = printer.best_known_values()
    if values:
        emit()
        print_objectives(values)
    return 1


def run_child(args: argparse.Namespace) -> int:
    input_path = Path(args.input_file)
    z3 = load_z3(args.z3_root, args.z3_exe)

    opt = z3.Optimize()
    if args.timeout_ms > 0:
        opt.set(timeout=args.timeout_ms)
    opt.from_file(str(input_path))

    directions = extract_objective_directions(input_path)
    objective_exprs = list(opt.objectives())
    printer = ProgressPrinter(z3, objective_exprs, directions)
    opt.set_on_model(printer.on_model)

    try:
        result = opt.check()
    except KeyboardInterrupt:
        printer.finish("interrupted")
        return print_unknown_result("interrupted", printer)
    except z3.Z3Exception as exc:
        status, reason = classify_z3_exception(exc)
        printer.finish(status)
        return print_unknown_result(reason, printer)
    except Exception as exc:
        printer.finish("error")
        return print_unknown_result(str(exc), printer)

    printer.finish("sat_optimal" if result == z3.sat else str(result))
    if result == z3.unknown:
        return print_unknown_result(opt.reason_unknown(), printer)
    return print_final_result(z3, opt, objective_exprs, result)


def main() -> int:
    args = parse_args()
    if args.child_mode:
        return run_child(args)
    return run_parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
