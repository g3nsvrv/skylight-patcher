# Modular SkyLight / WindowServer runtime patcher

Experimental, build-specific runtime patches for macOS SkyLight / WindowServer problems.

The repository is intentionally **modular**: the patch engine contains no hard-coded patch offsets. Every runtime modification lives in `patches/*.patch`, and the scripts only discover, validate, apply, verify, and reapply those definitions.

> [!WARNING]
> These are machine-code patches for **macOS Ventura 13.7.8, build 22H730, Intel/x86_64**. They are not Apple-supported fixes. The patcher refuses unknown builds/architectures and refuses to write if the bytes in memory are not exactly the expected original bytes (or the already-patched bytes).

## Repository layout

```text
.
├── README.md
├── install.sh
├── run.sh
├── uninstall.sh
├── patches/
│   ├── actual-host-time.patch
│   └── stage-manager-cglayerkit-null-connection.patch
└── scripts/
    ├── lldb_patch.py
    ├── local.skylight-patch.plist
    ├── patcher.py
    └── skylight-patch-monitor.sh
```

- `patches/*.patch`: declarative patch definitions. Adding a patch does not require changing the patch engine.
- `scripts/patcher.py`: discovers patches, checks host compatibility, selects target processes and invokes LLDB.
- `scripts/lldb_patch.py`: runs inside LLDB, verifies every byte range before changing memory, writes the plan, verifies the final bytes, then detaches.
- `scripts/skylight-patch-monitor.sh`: watches for new target PIDs and reapplies applicable patches.
- `scripts/local.skylight-patch.plist`: LaunchDaemon for automatic reapplication.
- Root shell scripts are the public entry points.

## Included patches

### `actual-host-time`

File: `patches/actual-host-time.patch`

Preserves the original patch for the repeated WindowServer/SkyLight message:

```text
Invalid actual_host_time received from display: ...
```

For build `22H730`, it changes the branch at SkyLight module offset `0x185DB4`:

```text
expected:    0f85a8010000    ; jne error path
replacement: 909090909090    ; NOP x6
```

The following timestamp-handling code remains in place.

### `stage-manager-cglayerkit-null-connection` (experimental)

File: `patches/stage-manager-cglayerkit-null-connection.patch`

This patch targets the WindowServer-side race observed with Stage Manager enabled:

```text
WindowServer:  CGLayerKit called back with no connection
WindowManager: CGSWindowShmemCreateWithPort failed on port 0
```

The first experimental revision guarded only the call inside `_CGLayerKitFetch` at module offset `0x1079CC`. Runtime testing showed that the error could still be reproduced with that call site patched. Re-checking the complete `22H730` disassembly shows that SkyLight invokes the **same helper** (`0x...3e46ae`) from exactly two call sites, and both reload a connection pointer immediately before the call without a local null check.

Call site 1, module offset `0x08CAE5`:

```asm
movq    0x10(%r13), %rsi
movq    %r15, %rdi
callq   <same CGLayerKit helper>
```

Original call bytes:

```text
e8c42b2e00
```

Call site 2, inside `_CGLayerKitFetch`, module offset `0x1079CC`:

```asm
movq    0x10(%r14), %rsi
movq    %rbx, %rdi
callq   <same CGLayerKit helper>
```

Original call bytes:

```text
e8dd7c2600
```

An existing 14-byte NOP area at module offset `0x107D9E` is used as a shared trampoline:

```asm
testq   %rsi, %rsi
je      no_connection
jmp     original_helper

no_connection:
ret
nop
nop
nop
```

Machine code:

```text
4885f67405e906792600c3909090
```

Both call sites are redirected to that trampoline:

```text
0x08CAE5: e8c42b2e00 -> e8b4b20700
0x1079CC: e8dd7c2600 -> e8cd030000
```

If the connection is still valid, the trampoline tail-jumps to the **same original helper**. If `%rsi` is null, it returns directly to the instruction following whichever original call site invoked it.

This patch does **not** NOP the helper, suppress the log function, or disable Stage Manager.

> [!IMPORTANT]
> This remains experimental. The second call-site guard was added only after runtime testing proved that guarding `_CGLayerKitFetch` alone did not eliminate the error. The patcher validates the exact original or already-patched bytes at all three locations before writing. The separate WindowManager-side `CGSWindowShmemCreateWithPort failed on port 0` path is not patched here.

## Requirements

### LLDB

```bash
xcrun -f lldb
```

If missing:

```bash
xcode-select --install
```

### Allow LLDB to attach to protected processes

From macOS Recovery:

```bash
csrutil enable --without debug
```

Then reboot normally.

Authenticated root does not need to be disabled because these patches only modify the in-memory copy loaded by the target process.

## List and validate patches

Neither command writes process memory:

```bash
python3 scripts/patcher.py --patch-dir patches --list
python3 scripts/patcher.py --patch-dir patches --validate
```

## Manual use

Apply every enabled compatible patch:

```bash
chmod +x run.sh
./run.sh
```

Apply only the Stage Manager patch:

```bash
./run.sh --only stage-manager-cglayerkit-null-connection
```

Apply only the original refresh/timestamp patch:

```bash
./run.sh --only actual-host-time
```

`run.sh` requests `sudo` when necessary.

A patch is applied only when all of these match:

- macOS build
- CPU architecture
- target process
- loaded module name
- exact expected bytes for every write

The LLDB helper validates the complete patch plan **before the first write**. It also accepts an already-patched byte sequence, making reapplication idempotent.

For the Stage Manager patch, the trampoline is written first and only then are both original call sites redirected to it.

## Install automatic reapplication

Runtime patches disappear when their target process restarts or the Mac reboots. Install the LaunchDaemon to reapply them automatically:

```bash
chmod +x install.sh uninstall.sh
sudo ./install.sh
```

Installed files:

```text
/usr/local/share/skylight-patcher/scripts/patcher.py
/usr/local/share/skylight-patcher/scripts/lldb_patch.py
/usr/local/share/skylight-patcher/patches/*.patch
/usr/local/sbin/skylight-patch-monitor.sh
/Library/LaunchDaemons/local.skylight-patch.plist
```

The monitor derives the process names it needs to watch from the patch definitions. A future `.patch` can therefore target another process (for example `WindowManager`) without hard-coding that process into the monitor.

### Daemon status

```bash
sudo launchctl print system/local.skylight-patch
```

### Logs

```bash
sudo tail -f /var/log/skylight-patch.log
```

## Verify the Stage Manager patch

Count WindowServer null-connection callbacks:

```bash
log show --last 5m --style compact \
  --predicate 'process == "WindowServer" AND eventMessage CONTAINS "CGLayerKit called back with no connection"' \
  | grep -c 'CGLayerKit called back with no connection'
```

Count the paired WindowManager shared-memory error independently:

```bash
log show --last 5m --style compact \
  --predicate 'process == "WindowManager" AND eventMessage CONTAINS "CGSWindowShmemCreateWithPort failed on port 0"' \
  | grep -c 'CGSWindowShmemCreateWithPort failed on port 0'
```

Check CPU at the same time:

```bash
ps -Ao pid,comm,%cpu | egrep 'WindowServer|WindowManager'
```

The useful A/B test is Stage Manager enabled before vs. after applying only `stage-manager-cglayerkit-null-connection` while leaving the workload otherwise unchanged.

## Patch file format

A `.patch` is JSON so the engine can validate it without executing patch-specific code:

```json
{
  "format": 1,
  "id": "example",
  "name": "Example patch",
  "enabled": true,
  "experimental": true,
  "target": {
    "process": "WindowServer",
    "module": "SkyLight",
    "builds": ["22H730"],
    "architectures": ["x86_64"]
  },
  "writes": [
    {
      "name": "example-write",
      "offset": "0x1234",
      "expected": "aabbccdd",
      "replacement": "11223344"
    }
  ]
}
```

All offsets are relative to the loaded module's Mach-O header address, so ASLR does not change the patch definition.

## Disable a patch

Set:

```json
"enabled": false
```

in that `.patch` file. No script changes are required.

For a one-off test, prefer `--only` instead of editing files.

## Uninstall

```bash
sudo ./uninstall.sh
```

This removes the LaunchDaemon and installed patch files. A patch already present in the current process remains in RAM until that process restarts; reboot once to guarantee all runtime modifications are gone.

## Safety properties

- build-specific and architecture-specific
- target process and loaded module are explicit per patch
- exact original-byte verification before writing
- full patch plan validated before the first write
- replacement bytes verified after writing
- already-applied state recognized safely
- overlapping writes rejected
- target detached even on failure
- no sealed-system-volume modification
- no dyld shared-cache modification on disk
- unsupported OS updates fail closed instead of receiving old offsets
