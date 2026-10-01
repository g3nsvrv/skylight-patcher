#!/usr/bin/env python3
"""Declarative runtime patch manager for SkyLight/WindowServer.

Patch definitions are JSON documents with a .patch extension.  This program
validates host compatibility, groups patches by target process, and delegates
memory reads/writes to LLDB.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PATCH_DIR = SCRIPT_DIR.parent / "patches"
LLDB_SCRIPT = SCRIPT_DIR / "lldb_patch.py"


class PatchError(RuntimeError):
    pass


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_patch(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PatchError(f"{path}: cannot parse patch: {exc}") from exc

    required = {"format", "id", "name", "enabled", "target", "writes"}
    missing = sorted(required - set(data))
    if missing:
        raise PatchError(f"{path}: missing fields: {', '.join(missing)}")
    if data["format"] != 1:
        raise PatchError(f"{path}: unsupported patch format {data['format']!r}")
    if not isinstance(data["id"], str) or not data["id"]:
        raise PatchError(f"{path}: id must be a non-empty string")
    if not isinstance(data["enabled"], bool):
        raise PatchError(f"{path}: enabled must be boolean")

    target = data["target"]
    for field in ("process", "module", "builds", "architectures"):
        if field not in target:
            raise PatchError(f"{path}: target.{field} is required")
    if not target["builds"] or not target["architectures"]:
        raise PatchError(f"{path}: builds/architectures cannot be empty")

    writes = data["writes"]
    if not isinstance(writes, list) or not writes:
        raise PatchError(f"{path}: writes must be a non-empty list")

    for idx, write in enumerate(writes):
        for field in ("name", "offset", "expected", "replacement"):
            if field not in write:
                raise PatchError(f"{path}: writes[{idx}].{field} is required")
        try:
            offset = int(write["offset"], 0)
        except Exception as exc:
            raise PatchError(
                f"{path}: writes[{idx}].offset is invalid: {write['offset']!r}"
            ) from exc
        if offset < 0:
            raise PatchError(f"{path}: writes[{idx}].offset cannot be negative")
        try:
            expected = bytes.fromhex(write["expected"])
            replacement = bytes.fromhex(write["replacement"])
        except Exception as exc:
            raise PatchError(f"{path}: writes[{idx}] contains invalid hex") from exc
        if not expected or len(expected) != len(replacement):
            raise PatchError(
                f"{path}: writes[{idx}] expected/replacement lengths must match and be non-zero"
            )

    data["_path"] = str(path)
    return data


def load_patches(patch_dir: Path) -> List[dict]:
    if not patch_dir.is_dir():
        raise PatchError(f"patch directory does not exist: {patch_dir}")
    paths = sorted(patch_dir.glob("*.patch"))
    if not paths:
        raise PatchError(f"no .patch files found in {patch_dir}")

    patches = [load_patch(path) for path in paths]
    ids = [patch["id"] for patch in patches]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise PatchError(f"duplicate patch id(s): {', '.join(duplicates)}")
    return patches


def host_build() -> str:
    try:
        return subprocess.check_output(
            ["/usr/bin/sw_vers", "-buildVersion"], text=True
        ).strip()
    except Exception as exc:
        raise PatchError(f"cannot determine macOS build: {exc}") from exc


def host_arch() -> str:
    return platform.machine()


def lldb_path() -> str:
    try:
        return subprocess.check_output(
            ["/usr/bin/xcrun", "-f", "lldb"], text=True
        ).strip()
    except Exception as exc:
        raise PatchError(
            "LLDB was not found. Install Xcode Command Line Tools with: xcode-select --install"
        ) from exc


def applicable_patches(
    patches: Sequence[dict], build: str, arch: str, only: Sequence[str]
) -> List[dict]:
    only_set = set(only)
    known = {patch["id"] for patch in patches}
    unknown = sorted(only_set - known)
    if unknown:
        raise PatchError(f"unknown patch id(s): {', '.join(unknown)}")

    selected = []
    for patch in patches:
        if only_set and patch["id"] not in only_set:
            continue
        if not patch["enabled"]:
            continue
        target = patch["target"]
        if build not in target["builds"]:
            continue
        if arch not in target["architectures"]:
            continue
        selected.append(patch)
    return selected


def pids_for_process(name: str) -> List[int]:
    proc = subprocess.run(
        ["/usr/bin/pgrep", "-x", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if proc.returncode not in (0, 1):
        raise PatchError(f"pgrep failed for {name} with exit code {proc.returncode}")
    pids = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return sorted(set(pids))


def group_by_process(patches: Sequence[dict]) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = {}
    for patch in patches:
        grouped.setdefault(patch["target"]["process"], []).append(patch)
    return grouped


def _lldb_quote(path: Path) -> str:
    value = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def apply_to_pid(lldb: str, pid: int, patches: Sequence[dict]) -> bool:
    plan = {"version": 1, "pid": pid, "patches": []}
    for patch in patches:
        clean = {key: value for key, value in patch.items() if not key.startswith("_")}
        plan["patches"].append(clean)

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", delete=False
    ) as handle:
        json.dump(plan, handle, indent=2)
        plan_path = Path(handle.name)

    try:
        cmd = [
            lldb,
            "-b",
            "-p",
            str(pid),
            "-o",
            f"command script import {_lldb_quote(LLDB_SCRIPT)}",
            "-o",
            f"apply_patch_plan {_lldb_quote(plan_path)}",
            "-o",
            "quit",
        ]
        print(f"Attaching to PID {pid} ({patches[0]['target']['process']})")
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output = proc.stdout or ""
        print(output, end="" if output.endswith("\n") else "\n")
        ok = "PATCH_PLAN_SUCCESS" in output and "PATCH_PLAN_FAILED:" not in output
        if not ok:
            eprint(f"Patch plan failed for PID {pid} (lldb exit {proc.returncode})")
        return ok
    finally:
        try:
            plan_path.unlink()
        except FileNotFoundError:
            pass


def print_patch_list(patches: Sequence[dict]) -> None:
    for patch in patches:
        target = patch["target"]
        flags = []
        flags.append("enabled" if patch["enabled"] else "disabled")
        if patch.get("experimental"):
            flags.append("experimental")
        print(
            f"{patch['id']}: {patch['name']} "
            f"[{', '.join(flags)}] target={target['process']}/{target['module']} "
            f"builds={','.join(target['builds'])} arch={','.join(target['architectures'])}"
        )


def ensure_root() -> None:
    if os.geteuid() != 0:
        raise PatchError("runtime patching must run as root")


def run_once(selected: Sequence[dict], lldb: str) -> bool:
    success = True
    for process_name, process_patches in group_by_process(selected).items():
        pids = pids_for_process(process_name)
        if not pids:
            eprint(f"Target process not running: {process_name}")
            success = False
            continue
        for pid in pids:
            if not apply_to_pid(lldb, pid, process_patches):
                success = False
    return success


def run_watch(selected: Sequence[dict], lldb: str, interval: float) -> int:
    grouped = group_by_process(selected)
    seen: Dict[str, set[int]] = {name: set() for name in grouped}
    print(
        "Watching target processes: "
        + ", ".join(sorted(grouped))
        + f" (interval {interval:g}s)"
    )

    while True:
        for process_name, process_patches in grouped.items():
            current = set(pids_for_process(process_name))
            new_pids = sorted(current - seen[process_name])
            for pid in new_pids:
                print(f"Detected new {process_name} PID {pid}")
                apply_to_pid(lldb, pid, process_patches)
            seen[process_name] = current
        time.sleep(interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--patch-dir", type=Path, default=DEFAULT_PATCH_DIR, help="directory containing *.patch"
    )
    parser.add_argument(
        "--only", action="append", default=[], metavar="PATCH_ID", help="apply only this patch (repeatable)"
    )
    parser.add_argument("--list", action="store_true", help="list patch definitions and exit")
    parser.add_argument("--validate", action="store_true", help="validate all patch files and exit")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="apply applicable patches once (default)")
    mode.add_argument("--watch", action="store_true", help="watch target PIDs and patch new processes")
    parser.add_argument(
        "--interval", type=float, default=2.0, help="watch interval in seconds (default: 2)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        patches = load_patches(args.patch_dir.resolve())
        if args.list:
            print_patch_list(patches)
            return 0
        if args.validate:
            print(f"Validated {len(patches)} patch file(s) in {args.patch_dir.resolve()}")
            return 0

        ensure_root()
        build = host_build()
        arch = host_arch()
        selected = applicable_patches(patches, build, arch, args.only)
        if not selected:
            raise PatchError(
                f"no enabled patches match macOS build {build} / architecture {arch}"
            )

        print(f"Host: macOS build {build}, architecture {arch}")
        print("Selected patches: " + ", ".join(p["id"] for p in selected))
        lldb = lldb_path()
        if not LLDB_SCRIPT.is_file():
            raise PatchError(f"missing LLDB helper: {LLDB_SCRIPT}")

        if args.watch:
            if args.interval < 0.5:
                raise PatchError("--interval must be at least 0.5 seconds")
            return run_watch(selected, lldb, args.interval)
        return 0 if run_once(selected, lldb) else 1

    except KeyboardInterrupt:
        return 130
    except PatchError as exc:
        eprint(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
