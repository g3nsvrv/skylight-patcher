# Ventura SkyLight / WindowServer CPU patch

Experimental runtime patch for a macOS WindowServer / SkyLight display-timing issue that can cause repeated errors such as:

```text
Invalid actual_host_time received from display: 0x... new time: ... last time: ...
```

On an affected Intel Mac, this message can be emitted several times per second. The suspected performance problem is not the log message by itself, but the fact that WindowServer repeatedly enters SkyLight's abnormal display-timing path, which calls `_SLSLogBreak` and the OS logging machinery over and over again.

This project was created to bypass that repeated error path and, in turn, reduce the associated **WindowServer CPU usage**.

On the tested system, applying the patch stopped the `Invalid actual_host_time received from display` flood immediately. The project is therefore intended for systems where high WindowServer CPU usage occurs together with this recurring SkyLight timing error.

> [!IMPORTANT]
> This should be described as a **display/frame timing problem**, not conclusively as a variable-refresh-rate bug. Similar reports are associated with frame presentation and refresh behavior, but the reverse-engineered Ventura code only proves that the failing path is validating display timing (`actual_host_time`).

> [!WARNING]
> This is a build-specific machine-code patch for **macOS Ventura 13.7.8 (22H730), x86_64**. It is experimental and is not an Apple-supported fix. The patcher refuses to run on another macOS build and verifies the original instruction bytes before writing anything.

## Related report

A matching error has been reported publicly in SDL issue [libsdl-org/SDL#11569](https://github.com/libsdl-org/SDL/issues/11569).

The report reproduces the same `Invalid actual_host_time received from display` behavior on an Intel **MacBook Pro 13-inch 2020, Four Thunderbolt 3 ports**, while an Apple Silicon M2 MacBook Air did not reproduce it.

That issue does not contain this patch. It is included as an independent public report of the same WindowServer / SkyLight behavior.

## What the patch changes

For Ventura 13.7.8 build `22H730`, the relevant SkyLight code contains the following instruction at Mach-O offset `0x185DB4`:

```text
0F 85 A8 01 00 00    jne <SkyLight error path>
```

The branch is reached from the display-present handler when SkyLight considers the new display timing invalid relative to the previous stored timing value.

The patch replaces the conditional jump with six NOP instructions:

```text
90 90 90 90 90 90
```

In simplified form:

```text
normal display-present handling
        |
        v
compare actual_host_time
        |
        +---- timing considered invalid ----> _SLSLogBreak
                                             os_log error
                                             repeated CPU work

                    becomes

normal display-present handling
        |
        v
compare actual_host_time
        |
        +---- continue through Apple's existing timestamp handling
```

The existing code immediately following the branch is left intact. The patch does not remove the entire timestamp handling routine; it only prevents execution from entering the repeated error/logging path.

## Why this may reduce WindowServer CPU usage

Before patching, the tested system produced roughly **5–6 `Invalid actual_host_time` errors per second** while WindowServer also showed unusually high CPU usage.

Reverse engineering showed that every occurrence could enter:

```text
_SLSLogBreak
    -> logging checks
    -> __os_log_error_impl
```

The patch removes that repeated branch. After applying it, the same WindowServer log test showed **zero occurrences** of the error during the observed period.

This project does **not** claim that all high WindowServer CPU usage is caused by this condition. It targets the specific case where high CPU usage coincides with the repeated `Invalid actual_host_time received from display` error.

## How it works

The patch is applied only to the copy of SkyLight already loaded in **WindowServer's memory**.

It does **not** modify:

- the sealed system volume
- the dyld shared cache
- the SkyLight binary on disk

Because it is an in-memory patch, it disappears when WindowServer restarts or the Mac reboots.

Two modes are provided:

- `run.sh` — apply the patch manually to the currently running WindowServer
- `install.sh` — install a LaunchDaemon that automatically reapplies it whenever WindowServer starts

Both modes use the same Python patch implementation under `scripts/`.

## Tested configuration

- macOS Ventura 13.7.8
- build `22H730`
- Intel / x86_64
- tested on MacBook Pro 13-inch 2020 (Four Thunderbolt 3 ports)

Other macOS builds are intentionally rejected because this patch relies on a specific machine-code offset and byte sequence.

## Requirements

### LLDB

Check that LLDB is available:

```bash
xcrun -f lldb
```

If it is missing, install the Xcode Command Line Tools:

```bash
xcode-select --install
```

### Allow LLDB to attach to WindowServer

By default, SIP prevents LLDB from attaching to the protected WindowServer process.

Boot into macOS Recovery, open Terminal, and disable only SIP's debugging restriction:

```bash
csrutil enable --without debug
```

Then reboot normally.

You do **not** need to disable authenticated root for this runtime patch.

## Quick install

Clone or download the repository, then run:

```bash
chmod +x install.sh uninstall.sh run.sh
sudo ./install.sh
```

That's it. The installer:

1. verifies macOS build `22H730`
2. verifies LLDB is available
3. installs the patcher and monitor
4. installs the LaunchDaemon
5. starts automatic monitoring

Installed files:

```text
/usr/local/share/skylight-patcher/skylight_patcher.py
/usr/local/sbin/skylight-patch-monitor.sh
/Library/LaunchDaemons/local.skylight-patch.plist
```

## Manual use

To apply the patch only to the currently running WindowServer:

```bash
./run.sh
```

The script verifies all of the following before writing memory:

- macOS build is exactly `22H730`
- the selected module filename is exactly `SkyLight`
- the instruction at `SkyLight __TEXT + 0x185DB4` is exactly `0f85a8010000`

A successful run looks like:

```text
Current bytes : 0f85a8010000
Expected      : 0f85a8010000
New bytes     : 909090909090
PATCH SUCCESSFUL
WindowServer detached successfully
```

If any validation fails, the patcher refuses to modify memory.

## Automatic mode

After running:

```bash
sudo ./install.sh
```

macOS launches `skylight-patch-monitor.sh` as a LaunchDaemon.

The monitor waits for WindowServer, applies the patch once to that PID, and then waits. If WindowServer restarts and receives a new PID, the patch is applied again automatically.

### Check daemon status

```bash
sudo launchctl print system/local.skylight-patch
```

### Watch patch logs

```bash
sudo tail -f /var/log/skylight-patch.log
```

After a fresh boot, a successful application should look similar to:

```text
Detected WindowServer PID 148
Attempting SkyLight patch
Current bytes : 0f85a8010000
New bytes     : 909090909090
PATCH SUCCESSFUL
WindowServer detached successfully
Patch verified for WindowServer PID 148
```

## Verify the fix

Inspect WindowServer logs:

```bash
log show --last 5m --style compact --predicate 'process == "WindowServer"'
```

Or check only for the targeted error:

```bash
log show --last 5m --style compact \
  --predicate 'process == "WindowServer" AND eventMessage CONTAINS "Invalid actual_host_time received from display"'
```

A successfully patched affected system should stop producing the repeated error.

To measure the other half of the problem — WindowServer CPU usage — compare before and after:

```bash
top -pid "$(pgrep -x WindowServer)" -stats pid,cpu,time,threads,command
```

## Uninstall

Run:

```bash
sudo ./uninstall.sh
```

This removes the LaunchDaemon and installed patch files.

The currently running WindowServer remains patched in RAM until it restarts. Reboot once after uninstalling to return SkyLight to its original machine code.

If you no longer need debugger access, restore normal SIP from Recovery:

```bash
csrutil enable
```

## Safety checks

The patcher is intentionally conservative:

- supports build `22H730` only
- matches the actual `SkyLight` module, not merely a path containing `SkyLight`
- verifies the exact original machine-code bytes before writing
- verifies the patched bytes after writing
- attempts to detach WindowServer even when patching fails
- modifies RAM only
- does not patch the dyld cache or system volume
- refuses to patch an updated or otherwise different SkyLight binary