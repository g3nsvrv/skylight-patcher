import lldb
import subprocess
import traceback

SUPPORTED_BUILD = "22H730"
OFFSET = 0x185DB4
OLD = bytes.fromhex("0f 85 a8 01 00 00")
NEW = bytes.fromhex("90 90 90 90 90 90")


def system_build():
    try:
        return subprocess.check_output(
            ["/usr/bin/sw_vers", "-buildVersion"],
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def module_path(module):
    fs = module.GetFileSpec()
    directory = fs.GetDirectory() or ""
    filename = fs.GetFilename() or ""
    return (directory + "/" + filename) if directory else filename


def find_skylight(target):
    candidates = [
        "/System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/SkyLight",
        "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight",
    ]

    for candidate in candidates:
        module = target.FindModule(lldb.SBFileSpec(candidate))
        if module.IsValid():
            filename = module.GetFileSpec().GetFilename() or ""
            if filename == "SkyLight":
                return module

    for module in target.modules:
        filename = module.GetFileSpec().GetFilename() or ""
        if filename == "SkyLight":
            return module

    return None


debugger = lldb.debugger
target = debugger.GetSelectedTarget()
process = target.GetProcess()
failure = None

try:
    build = system_build()
    if build != SUPPORTED_BUILD:
        raise RuntimeError(
            "Unsupported macOS build: %s (expected %s). "
            "Refusing to apply a build-specific machine-code patch."
            % (build, SUPPORTED_BUILD)
        )

    if not process.IsValid():
        raise RuntimeError("No valid process attached")

    skylight = find_skylight(target)
    if skylight is None:
        raise RuntimeError("Actual SkyLight module not found")

    filename = skylight.GetFileSpec().GetFilename() or ""
    if filename != "SkyLight":
        raise RuntimeError(
            "Safety check failed: selected module is '%s', not 'SkyLight'"
            % filename
        )

    text = skylight.FindSection("__TEXT")
    if not text.IsValid():
        raise RuntimeError("SkyLight __TEXT section not found")

    base = text.GetLoadAddress(target)
    if base == lldb.LLDB_INVALID_ADDRESS:
        raise RuntimeError("Could not determine SkyLight __TEXT runtime address")

    address = base + OFFSET
    error = lldb.SBError()
    current = process.ReadMemory(address, len(OLD), error)

    if not error.Success():
        raise RuntimeError("ReadMemory failed: %s" % error.GetCString())

    print("macOS build   : %s" % build)
    print("SkyLight path : %s" % module_path(skylight))
    print("SkyLight base : 0x%x" % base)
    print("Patch offset  : 0x%x" % OFFSET)
    print("Patch address : 0x%x" % address)
    print("Current bytes : %s" % current.hex())
    print("Expected      : %s" % OLD.hex())

    if current == NEW:
        print("SkyLight already patched")
    elif current == OLD:
        error = lldb.SBError()
        written = process.WriteMemory(address, NEW, error)

        if not error.Success():
            raise RuntimeError("WriteMemory failed: %s" % error.GetCString())
        if written != len(NEW):
            raise RuntimeError("Partial write: %d/%d" % (written, len(NEW)))

        error = lldb.SBError()
        verify = process.ReadMemory(address, len(NEW), error)
        if not error.Success():
            raise RuntimeError("Verification read failed: %s" % error.GetCString())
        if verify != NEW:
            raise RuntimeError("Verification failed: %s" % verify.hex())

        print("New bytes     : %s" % verify.hex())
        print("PATCH SUCCESSFUL")
    else:
        raise RuntimeError(
            "REFUSING TO PATCH: expected %s but found %s at 0x%x"
            % (OLD.hex(), current.hex(), address)
        )

except Exception as exc:
    failure = exc
    print("PATCH ERROR:")
    traceback.print_exc()

finally:
    # WindowServer freezes while LLDB has it stopped. Always detach before
    # returning control, including on safety-check or patch failures.
    try:
        if process.IsValid():
            error = process.Detach()
            if error.Success():
                print("WindowServer detached successfully")
            else:
                print("WARNING: detach failed: %s" % error.GetCString())
    except Exception:
        traceback.print_exc()

if failure is not None:
    raise failure
