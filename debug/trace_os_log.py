#!/usr/bin/env python3
"""LLDB helper: stop on one os_log error whose format string matches a substring."""

import lldb
import shlex

_MATCH = None
_MATCH_THREAD_ID = None
_MATCH_FORMAT = None


def _read_format(frame):
    # x86_64 SysV / Darwin ABI for:
    # _os_log_error_impl(dso, log, type, format, buf, size)
    reg = frame.FindRegister("rcx")
    if not reg.IsValid():
        return None
    addr = reg.GetValueAsUnsigned()
    if not addr:
        return None
    err = lldb.SBError()
    s = frame.GetThread().GetProcess().ReadCStringFromMemory(addr, 4096, err)
    if err.Fail():
        return None
    return s


def _callback(frame, bp_loc, internal_dict):
    global _MATCH_THREAD_ID, _MATCH_FORMAT
    fmt = _read_format(frame)
    if fmt is None or _MATCH not in fmt:
        return False
    _MATCH_THREAD_ID = frame.GetThread().GetThreadID()
    _MATCH_FORMAT = fmt
    return True


def _module_base(module, target):
    sec = module.FindSection("__TEXT")
    if sec.IsValid():
        addr = sec.GetLoadAddress(target)
        if addr != lldb.LLDB_INVALID_ADDRESS:
            return addr
    return None


def _print_backtrace(result, target, thread):
    result.AppendMessage("MATCHED_OS_LOG")
    result.AppendMessage(f"format: {_MATCH_FORMAT}")
    result.AppendMessage(f"thread-id: 0x{thread.GetThreadID():x}")
    result.AppendMessage("backtrace:")
    for idx in range(min(thread.GetNumFrames(), 40)):
        frame = thread.GetFrameAtIndex(idx)
        pc = frame.GetPC()
        module = frame.GetModule()
        module_name = module.GetFileSpec().GetFilename() if module.IsValid() else "?"
        func = frame.GetFunctionName() or frame.GetDisplayFunctionName() or "?"
        base = _module_base(module, target) if module.IsValid() else None
        if base is not None and pc >= base:
            loc = f"{module_name}+0x{pc-base:x}"
        else:
            loc = f"0x{pc:x}"
        result.AppendMessage(f"  #{idx:02d} {loc:<32} {func}")


def trace_os_log_once(debugger, command, result, internal_dict):
    global _MATCH, _MATCH_THREAD_ID, _MATCH_FORMAT
    try:
        args = shlex.split(command)
    except ValueError as exc:
        result.SetError(f"invalid arguments: {exc}")
        return
    if not args:
        result.SetError('usage: trace_os_log_once "substring"')
        return

    _MATCH = " ".join(args)
    _MATCH_THREAD_ID = None
    _MATCH_FORMAT = None

    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    if not target.IsValid() or not process.IsValid():
        result.SetError("no valid target/process")
        return

    bp = target.BreakpointCreateByName("_os_log_error_impl", "libsystem_trace.dylib")
    if not bp.IsValid() or bp.GetNumLocations() == 0:
        bp = target.BreakpointCreateByName("_os_log_error_impl")
    if not bp.IsValid() or bp.GetNumLocations() == 0:
        result.SetError("could not resolve _os_log_error_impl")
        return

    bp.SetScriptCallbackFunction(__name__ + "._callback")
    result.AppendMessage(f"Tracing _os_log_error_impl for: {_MATCH}")
    result.AppendMessage("Trigger the Stage Manager action that produces the error...")

    err = process.Continue()
    if err.Fail():
        target.BreakpointDelete(bp.GetID())
        result.SetError(f"continue failed: {err.GetCString()}")
        return

    if _MATCH_THREAD_ID is None:
        target.BreakpointDelete(bp.GetID())
        result.SetError(f"process stopped before matching log; state={process.GetState()}")
        return

    matched_thread = None
    for i in range(process.GetNumThreads()):
        t = process.GetThreadAtIndex(i)
        if t.GetThreadID() == _MATCH_THREAD_ID:
            matched_thread = t
            break
    if matched_thread is None:
        matched_thread = process.GetSelectedThread()

    _print_backtrace(result, target, matched_thread)
    target.BreakpointDelete(bp.GetID())

    detach_err = process.Detach()
    if detach_err.Fail():
        result.AppendWarning(f"detach failed: {detach_err.GetCString()}")
    else:
        result.AppendMessage("Target detached successfully")


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand(
        f"command script add -f {__name__}.trace_os_log_once trace_os_log_once"
    )
