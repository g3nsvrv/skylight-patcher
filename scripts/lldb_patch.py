"""LLDB-side patch executor.

Imported by scripts/patcher.py into LLDB.  The normal Python interpreter does
not need the lldb module; only this file runs inside LLDB.
"""

from __future__ import print_function

import json
import shlex

import lldb


def _fail(result, message):
    print("PATCH_PLAN_FAILED: {}".format(message))
    result.SetError(message)


def _find_module(target, wanted):
    for module in target.module_iter():
        fs = module.GetFileSpec()
        if fs.GetFilename() == wanted:
            return module
        full = str(fs)
        if full.endswith("/" + wanted):
            return module
    return None


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    if not error.Success():
        raise RuntimeError(
            "read failed at 0x{:x}: {}".format(address, error.GetCString())
        )
    if isinstance(data, str):
        data = data.encode("latin1")
    return bytes(data)


def _write(process, address, data):
    error = lldb.SBError()
    written = process.WriteMemory(address, data, error)
    if not error.Success() or written != len(data):
        raise RuntimeError(
            "write failed at 0x{:x}: wrote {}/{} bytes: {}".format(
                address, written, len(data), error.GetCString()
            )
        )


def _parse_hex(value, field):
    try:
        return bytes.fromhex(value)
    except Exception as exc:
        raise RuntimeError("invalid {} hex value {!r}: {}".format(field, value, exc))


def _ranges_overlap(a_start, a_len, b_start, b_len):
    return a_start < b_start + b_len and b_start < a_start + a_len


def apply_patch_plan(debugger, command, result, internal_dict):
    argv = shlex.split(command)
    if len(argv) != 1:
        _fail(result, "usage: apply_patch_plan /path/to/plan.json")
        return

    plan_path = argv[0]
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    detached = False

    try:
        with open(plan_path, "r") as handle:
            plan = json.load(handle)

        patches = plan.get("patches", [])
        if not patches:
            raise RuntimeError("patch plan is empty")

        prepared = []
        occupied = []

        # Validate every patch and every byte range before writing anything.
        for patch in patches:
            patch_id = patch["id"]
            module_name = patch["target"]["module"]
            module = _find_module(target, module_name)
            if module is None:
                raise RuntimeError(
                    "{}: module {!r} is not loaded".format(patch_id, module_name)
                )

            base = module.GetObjectFileHeaderAddress().GetLoadAddress(target)
            if base == lldb.LLDB_INVALID_ADDRESS:
                raise RuntimeError(
                    "{}: could not resolve load address for {}".format(
                        patch_id, module_name
                    )
                )

            print(
                "Patch {}: {} base = 0x{:x}".format(
                    patch_id, module_name, base
                )
            )

            for write in patch["writes"]:
                offset = int(write["offset"], 0)
                expected = _parse_hex(write["expected"], "expected")
                replacement = _parse_hex(write["replacement"], "replacement")
                if len(expected) != len(replacement):
                    raise RuntimeError(
                        "{} / {}: expected and replacement sizes differ".format(
                            patch_id, write["name"]
                        )
                    )

                address = base + offset
                for other_address, other_len, other_desc in occupied:
                    if _ranges_overlap(address, len(expected), other_address, other_len):
                        raise RuntimeError(
                            "{} / {} overlaps {}".format(
                                patch_id, write["name"], other_desc
                            )
                        )
                occupied.append(
                    (address, len(expected), "{} / {}".format(patch_id, write["name"]))
                )

                current = _read(process, address, len(expected))
                if current == expected:
                    state = "pending"
                elif current == replacement:
                    state = "already-patched"
                else:
                    raise RuntimeError(
                        "{} / {}: bytes at {}+{} are {}, expected {} or {}".format(
                            patch_id,
                            write["name"],
                            module_name,
                            write["offset"],
                            current.hex(),
                            expected.hex(),
                            replacement.hex(),
                        )
                    )

                print(
                    "  {:<46} {:>15}  current={}".format(
                        write["name"], state, current.hex()
                    )
                )
                prepared.append(
                    {
                        "patch_id": patch_id,
                        "name": write["name"],
                        "address": address,
                        "replacement": replacement,
                        "state": state,
                    }
                )

        # Writes remain in .patch file order.  The Stage Manager patch places
        # its trampoline first and redirects the call only after it exists.
        for item in prepared:
            if item["state"] == "pending":
                _write(process, item["address"], item["replacement"])
                print(
                    "  wrote {} / {} at 0x{:x}".format(
                        item["patch_id"], item["name"], item["address"]
                    )
                )

        # Verify every replacement after all writes.
        for item in prepared:
            current = _read(process, item["address"], len(item["replacement"]))
            if current != item["replacement"]:
                raise RuntimeError(
                    "verification failed for {} / {} at 0x{:x}: {} != {}".format(
                        item["patch_id"],
                        item["name"],
                        item["address"],
                        current.hex(),
                        item["replacement"].hex(),
                    )
                )

        print("PATCH_PLAN_SUCCESS")
        result.SetStatus(lldb.eReturnStatusSuccessFinishResult)

    except Exception as exc:
        _fail(result, str(exc))

    finally:
        if process and process.IsValid() and process.GetState() != lldb.eStateDetached:
            error = process.Detach()
            if error.Success():
                detached = True
                print("Target detached successfully")
            else:
                print("WARNING: detach failed: {}".format(error.GetCString()))
        if not detached and process and process.IsValid():
            # LLDB will also detach when it exits; this line makes the state
            # explicit in the output when Detach() was not successful.
            print("Target detach will be retried by LLDB shutdown")


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand(
        "command script add -f lldb_patch.apply_patch_plan apply_patch_plan"
    )
