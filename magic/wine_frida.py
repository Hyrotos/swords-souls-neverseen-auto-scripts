"""Make a Wine/Proton process attachable for Frida on Linux.

Wine starts every process through a statically linked preloader, so the
auxiliary vector the kernel reports in /proc/<pid>/auxv names no dynamic
linker (AT_BASE is 0).  Frida then treats the game as a static executable,
cannot find its libc and aborts inside the game, which terminates it.

The fix is to store the real ld.so base in the kernel's copy of the auxiliary
vector.  Only the process itself may do that, so one prctl() call is executed
in the game through ptrace before Frida attaches.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import time

_PTRACE_GETREGS = 12
_PTRACE_SETREGS = 13
_PTRACE_SINGLESTEP = 9
_PTRACE_DETACH = 17
_PTRACE_SEIZE = 0x4206
_PTRACE_INTERRUPT = 0x4207
_WALL = 0x40000000

_SYS_MMAP = 9
_SYS_MUNMAP = 11
_SYS_PRCTL = 157
_PR_SET_MM = 35
_PR_SET_MM_MAP = 14
_AT_BASE = 7

# Wine traps system calls issued below this address (syscall user dispatch),
# so the borrowed "syscall" instruction must come from a native library.
_NATIVE_CODE_START = 0x7000_0000_0000


class _Registers(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "r15", "r14", "r13", "r12", "rbp", "rbx", "r11", "r10", "r9",
            "r8", "rax", "rcx", "rdx", "rsi", "rdi", "orig_rax", "rip", "cs",
            "eflags", "rsp", "ss", "fs_base", "gs_base", "ds", "es", "fs",
            "gs",
        )
    ]


def _auxv_entries(data: bytes) -> list[tuple[int, int]]:
    return [
        struct.unpack_from("<QQ", data, offset)
        for offset in range(0, len(data) - 15, 16)
    ]


def _read_maps(pid: int) -> list[tuple[int, int, str, int, str]]:
    regions = []
    with open(f"/proc/{pid}/maps", encoding="utf-8", errors="replace") as maps:
        for line in maps:
            fields = line.rstrip("\n").split(None, 5)
            start, end = (int(value, 16) for value in fields[0].split("-"))
            path = fields[5] if len(fields) == 6 else ""
            regions.append((start, end, fields[1], int(fields[2], 16), path))
    return regions


class _Tracer:
    """Run single system calls in one stopped thread of another process."""

    def __init__(self, pid: int, syscall_address: int) -> None:
        self._libc = ctypes.CDLL(None, use_errno=True)
        self._libc.ptrace.restype = ctypes.c_long
        self._libc.ptrace.argtypes = [
            ctypes.c_long, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p
        ]
        self._tid = pid
        self._syscall_address = syscall_address
        self._saved = _Registers()
        self._ptrace(_PTRACE_SEIZE, None)
        try:
            self._ptrace(_PTRACE_INTERRUPT, None)
            os.waitpid(pid, _WALL)
            self._ptrace(_PTRACE_GETREGS, ctypes.byref(self._saved))
        except BaseException:
            self._libc.ptrace(_PTRACE_DETACH, pid, None, None)
            raise

    def _ptrace(self, request: int, data) -> None:
        ctypes.set_errno(0)
        if self._libc.ptrace(request, self._tid, None, data) == -1:
            error = ctypes.get_errno()
            raise OSError(error, f"ptrace({request:#x}): {os.strerror(error)}")

    def syscall(self, number: int, *args: int) -> int:
        registers = _Registers.from_buffer_copy(self._saved)
        values = list(args) + [0] * (6 - len(args))
        registers.rax = number
        (registers.rdi, registers.rsi, registers.rdx,
         registers.r10, registers.r8, registers.r9) = values
        registers.rip = self._syscall_address
        # Not "inside a system call": the kernel must not try to restart the
        # call this thread was interrupted in while the borrowed one runs.
        registers.orig_rax = 2**64 - 1
        self._ptrace(_PTRACE_SETREGS, ctypes.byref(registers))
        self._ptrace(_PTRACE_SINGLESTEP, None)
        _pid, status = os.waitpid(self._tid, _WALL)
        if not os.WIFSTOPPED(status):
            raise RuntimeError("the game exited while it was being prepared")
        self._ptrace(_PTRACE_GETREGS, ctypes.byref(registers))
        result = ctypes.c_longlong(registers.rax).value
        if -4096 < result < 0:
            raise OSError(-result, f"remote syscall {number}: {os.strerror(-result)}")
        return result

    def close(self) -> None:
        try:
            self._ptrace(_PTRACE_SETREGS, ctypes.byref(self._saved))
        finally:
            self._libc.ptrace(_PTRACE_DETACH, self._tid, None, None)


def prepare_for_frida(pid: int) -> bool:
    """Publish the dynamic linker of a Wine process; return True if changed."""
    with open(f"/proc/{pid}/auxv", "rb") as file:
        auxv = bytearray(file.read())
    if not any(kind == _AT_BASE and value == 0 for kind, value in _auxv_entries(auxv)):
        return False

    regions = _read_maps(pid)
    linker = [
        start for start, _end, _perms, offset, path in regions
        if offset == 0 and os.path.basename(path).startswith("ld-linux")
    ]
    if not linker:
        raise RuntimeError("the dynamic linker is not mapped in the game process")
    for offset in range(0, len(auxv) - 15, 16):
        if struct.unpack_from("<Q", auxv, offset)[0] == _AT_BASE:
            struct.pack_into("<Q", auxv, offset + 8, min(linker))

    syscall_address = None
    with open(f"/proc/{pid}/mem", "rb", buffering=0) as memory:
        for start, end, perms, _offset, path in regions:
            if (
                start < _NATIVE_CODE_START
                or "x" not in perms
                or not os.path.basename(path).startswith("libc.so")
            ):
                continue
            memory.seek(start)
            index = memory.read(end - start).find(b"\x0f\x05")
            if index >= 0:
                syscall_address = start + index
                break
    if syscall_address is None:
        raise RuntimeError("no usable syscall instruction found in the game's libc")

    # prctl(PR_SET_MM_MAP) replaces the whole memory-layout record, so every
    # other field is passed back unchanged.
    with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as file:
        stat = file.read().rsplit(")", 1)[1].split()

    def field(number: int) -> int:
        return int(stat[number - 3])

    heap = [end for _start, end, _perms, _offset, path in regions if path == "[heap]"]
    start_brk = field(47)
    brk = max(heap[0], start_brk) if heap else start_brk

    tracer = _Tracer(pid, syscall_address)
    try:
        # PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS
        scratch = tracer.syscall(_SYS_MMAP, 0, 4096, 3, 0x22, 2**64 - 1, 0)
        try:
            layout = struct.pack(
                "<11QQII",
                field(26), field(27),       # start_code, end_code
                field(45), field(46),       # start_data, end_data
                start_brk, brk,
                field(28),                  # start_stack
                field(48), field(49),       # arg_start, arg_end
                field(50), field(51),       # env_start, env_end
                scratch + 256, len(auxv),
                0xFFFFFFFF,                 # exe_fd: keep the executable link
            )
            with open(f"/proc/{pid}/mem", "r+b", buffering=0) as memory:
                memory.seek(scratch)
                memory.write(layout)
                memory.seek(scratch + 256)
                memory.write(bytes(auxv))
            tracer.syscall(
                _SYS_PRCTL, _PR_SET_MM, _PR_SET_MM_MAP, scratch, len(layout), 0
            )
        finally:
            tracer.syscall(_SYS_MUNMAP, scratch, 4096)
    finally:
        tracer.close()
    return True


# Shared start of the Frida agents.  It hides the three differences between a
# native Windows game and the same game inside Wine: Frida does not list PE
# modules there, Mono code uses the Windows x64 calling convention, and
# Frida's own threads have no Windows TEB, so they must not call into Mono.
AGENT_HEAD = r"""
const onWine = WINE_MONO_BASE !== null;
const abi = onWine ? 'win64' : 'default';

function peExports(base) {
    if (base.readU16() !== 0x5a4d) throw new Error('mono.dll: bad DOS header');
    const header = base.add(base.add(0x3c).readU32());
    if (header.readU32() !== 0x4550) throw new Error('mono.dll: bad PE header');
    const optional = header.add(24);
    if (optional.readU16() !== 0x20b) throw new Error('mono.dll is not 64-bit');
    const directory = base.add(optional.add(112).readU32());
    const count = directory.add(24).readU32();
    const functions = base.add(directory.add(28).readU32());
    const names = base.add(directory.add(32).readU32());
    const ordinals = base.add(directory.add(36).readU32());
    const table = new Map();
    for (let index = 0; index < count; ++index) {
        const name = base.add(names.add(index * 4).readU32()).readCString();
        const ordinal = ordinals.add(index * 2).readU16();
        table.set(name, base.add(functions.add(ordinal * 4).readU32()));
    }
    return table;
}

const mono = onWine ? null : Process.getModuleByName('mono.dll');
const monoExports = onWine ? peExports(ptr(WINE_MONO_BASE)) : null;

function exported(name) {
    const address = onWine
        ? (monoExports.get(name) || null)
        : mono.findExportByName(name);
    if (address === null) throw new Error('Mono export missing: ' + name);
    return address;
}

function native(name, result, args) {
    return new NativeFunction(exported(name), result, args, abi);
}

// A destroyed UnityEngine.Object (for example after its training scene was
// left) keeps its managed shell until the garbage collector removes it, but
// its native pointer (m_CachedPtr, the first field) is cleared.  Calling a
// Unity method on it raises a managed exception that cannot unwind through
// Frida and terminates the game, so every such call is checked first.
const unityClassCache = new Map();
let unityClassLookup = null;

// Returns the object's class, or NULL when the memory no longer holds a
// managed object (a collected object is zeroed).  The low bit of the vtable
// pointer is masked because Unity's liveness walk uses it as a mark.
function managedClass(object) {
    const vtable = object.readPointer().and(ptr(1).not());
    return vtable.isNull() ? ptr(0) : vtable.readPointer();
}

function isUnityClass(klass) {
    if (unityClassLookup === null) {
        unityClassLookup = {
            getParent: native('mono_class_get_parent', 'pointer', ['pointer']),
            getName: native('mono_class_get_name', 'pointer', ['pointer']),
            getNamespace: native(
                'mono_class_get_namespace', 'pointer', ['pointer'])
        };
    }
    const lookup = unityClassLookup;
    const key = klass.toString();
    let result = unityClassCache.get(key);
    if (result === undefined) {
        result = false;
        for (let current = klass; !current.isNull();
                current = lookup.getParent(current)) {
            if (lookup.getName(current).readUtf8String() === 'Object' &&
                    lookup.getNamespace(current).readUtf8String() ===
                        'UnityEngine') {
                result = true;
                break;
            }
        }
        unityClassCache.set(key, result);
    }
    return result;
}

function unityAlive(object) {
    if (object.isNull()) return false;
    const klass = managedClass(object);
    if (klass.isNull()) return false;
    if (!isUnityClass(klass)) return true;
    return !object.add(2 * Process.pointerSize).readPointer().isNull();
}

// Wraps a compiled instance method so that it is skipped, returning a neutral
// value, when its object has been destroyed.
function guarded(method, resultType) {
    const fallback = resultType === 'pointer' ? ptr(0)
        : (resultType === 'void' ? undefined
            : (resultType === 'float' ? NaN : 0));
    return function (self) {
        return unityAlive(self) ? method.apply(null, arguments) : fallback;
    };
}

// Interceptor maps args[] with the host ABI, so under Wine the Windows x64
// argument registers are read directly.
const winArgumentRegisters = ['rcx', 'rdx', 'r8', 'r9'];
function hookArgument(invocation, args, index) {
    return onWine
        ? invocation.context[winArgumentRegisters[index]]
        : args[index];
}

// start() looks up everything in Mono and returns the polling function, which
// is then called about every intervalMs milliseconds.
function runOnMonoThread(start, intervalMs) {
    if (!onWine) {
        native('mono_thread_attach', 'pointer', ['pointer'])(
            native('mono_get_root_domain', 'pointer', [])());
        setInterval(start(), intervalMs);
        return;
    }
    // The work piggybacks on the game's own managed calls instead of a timer.
    let poll = null;
    let failed = false;
    let busy = false;
    let hooked = false;
    let lastPoll = 0;
    Interceptor.attach(exported('mono_runtime_invoke'), {
        onEnter() {
            // Unity objects may only be used from the game's main thread;
            // Mono's finalizer thread comes through here as well.
            if (Process.getCurrentThreadId() !== Process.id) return;
            hooked = true;
            if (failed || busy) return;
            const now = Date.now();
            if (now - lastPoll < intervalMs) return;
            lastPoll = now;
            busy = true;
            try {
                if (poll === null) {
                    poll = start();
                } else {
                    poll();
                }
            } catch (error) {
                if (poll !== null) throw error;
                failed = true;
                send({kind: 'runtimeError', message: String(error)});
            } finally {
                busy = false;
            }
        }
    });
    setTimeout(function () {
        if (!hooked) {
            send({
                kind: 'runtimeError',
                message: 'the game never called mono_runtime_invoke on its main thread'
            });
        }
    }, 5000);
}
"""


def mono_base(pid: int) -> int:
    """Return where Wine mapped the PE header of the game's mono.dll."""
    for start, _end, _perms, offset, path in _read_maps(pid):
        if offset == 0 and path.casefold().endswith("/mono.dll"):
            return start
    raise RuntimeError("mono.dll is not mapped in the game process")


def agent_source(pid: int, body: str) -> str:
    """Build a complete agent; call this before frida.attach(pid)."""
    if sys.platform == "win32":
        base = "null"
    else:
        base = f"'{mono_base(pid):#x}'"
        # Without this Frida aborts inside the game and terminates it.
        prepare_for_frida(pid)
    return f"const WINE_MONO_BASE = {base};\n{AGENT_HEAD}\n{body}"


def print_attach_hint(error: Exception) -> None:
    """Explain the usual reason attaching to the game is denied on Linux."""
    if not isinstance(error, PermissionError):
        return
    try:
        with open("/proc/sys/kernel/yama/ptrace_scope", encoding="ascii") as file:
            scope = int(file.read().strip())
    except (OSError, ValueError):
        return
    if scope != 0:
        print(
            f"kernel.yama.ptrace_scope is {scope}, which blocks attaching to "
            "the running game. Allow it until the next reboot with:"
        )
        print("    sudo sysctl kernel.yama.ptrace_scope=0")


class AgentErrors:
    """Report errors raised inside a Frida agent, with their origin."""

    def __init__(self, on_status, interval: float = 2.0) -> None:
        self._on_status = on_status
        self._interval = interval
        self._last: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def report(self, message: dict) -> None:
        text = str(message.get("description", "unknown Frida agent error"))
        frames = [
            line.strip().removeprefix("at ")
            for line in str(message.get("stack") or "").splitlines()[1:4]
        ]
        if frames:
            text += " [" + " < ".join(frames) + "]"
        # A failing poll repeats on every pass; show each error only now and
        # then, with the number of repetitions in between.
        now = time.monotonic()
        if now - self._last.get(text, -self._interval) < self._interval:
            self._suppressed[text] = self._suppressed.get(text, 0) + 1
            return
        repeats = self._suppressed.pop(text, 0)
        self._last[text] = now
        suffix = f" (+{repeats} more)" if repeats else ""
        self._on_status(f"Memory reader error: {text}{suffix}")
