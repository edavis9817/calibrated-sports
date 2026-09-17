"""One capture process per store. An OS lock, not a PID file.

THE INCIDENT THIS EXISTS TO PREVENT. On 2026-09-11 two `cfb_probe.py` instances
ran at once. Both appended to the same hourly `.jsonl.gz`: the CFB poll rate
went from 58 game polls/hour to 117 - exactly double - and 43 of 114 shards
failed `gunzip -t`, the first corrupt file being the UTC hour the second process
started. Nothing detected it. `audit_shards()` checks that every file on disk
has a manifest row and `seal_shards()` hashes bytes; neither opens the stream,
so an unreadable archive passes both and invariant 2 - "every derivation must be
re-runnable from the archive" - is asserted but not verified.

WHY THE LAUNCHER WAS NOT ENOUGH. `start_logger.ps1` refuses a duplicate at line
64, but that is the launcher refusing, not the process. `python run_logger.py`
typed directly was never refused, and a probe started exactly that way is what
corrupted the archive. A guard that only one of the two entry paths honours is
not a guard.

WHY AN ADVISORY LOCK AND NOT A PID FILE. A PID file left behind by a process
that was killed is indistinguishable from one held by a process that is alive,
so every PID-file scheme must choose between refusing forever after a crash and
trusting a number the OS is free to reuse. An advisory lock has neither failure
mode: the kernel releases it when the holder dies, however it dies - SIGKILL,
power loss, a closed console - and it cannot be inherited by an unrelated
process that happens to land on the same pid. That property is the whole reason
this module exists, and `tests/test_single_instance.py` asserts it against a
real process rather than a mock.

THE FILE LAYOUT, AND WHY IT IS NOT JUST JSON. Byte 0 is the lock region and
nothing else; the holder's diagnostics are UTF-8 JSON from byte 1 onward. They
are split because a Windows lock is MANDATORY, not advisory: while byte 0 is
held, another process cannot read byte 0 at all. Keeping the lock byte disjoint
from the payload is what lets a refused process still open the file and name who
beat it to it, which is the difference between "already running" and "already
running - pid 2928 since 2026-09-15T01:10:23Z".

KEYED ON THE STORE, NOT ON THE CHECKOUT. The path comes from
`config.storage_path`, so two checkouts pointed at one store block each other -
which is correct, because the thing they would corrupt is the store's shard tree
- while two stores never do.

THE VENV REDIRECTOR IS NOT A SECOND INSTANCE, AND THAT IS MEASURED, NOT ASSUMED.
On this dev box `.venv\\Scripts\\python.exe` re-execs the base interpreter, so
`run_logger.py` shows up as two PIDs with identical command lines while the
module imports no `multiprocessing` and the child carries no fork marker.

The parent loads **7 modules and NO python DLL** - no `python314.dll`, no
`python3.dll` - at 0s CPU, 1 thread, 3.4 MB. The child loads `python314.dll`
across 72 modules, 81 threads, 3,210s CPU, 320 MB. The launcher therefore cannot
execute Python and can never reach `acquire()`: the lock is taken exactly once,
by the worker.

This matters because the obvious fear is the opposite - that a lock here would
make the child refuse and break the launcher pattern, taking the logger down
mid-season. It was checked by running this module through the logger's own
`.venv` interpreter: 1 python process ran, 1 acquisition, 0 refusals.

The same stub invalidates `Popen.pid` in tests. It returns the LAUNCHER, not the
process that runs the code, so a test asserting on it compares the wrong number -
and would pass on CI's Linux, which is the worst combination. Ask the holder who
it is; see `tests/test_single_instance.py`.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import config

# Bytes [0, 1) are the lock region. Diagnostics start here. See the module
# docstring: on Windows the locked region is unreadable to anyone else, so the
# two must not overlap.
LOCK_BYTE = 1

# Names, so a caller cannot key a lock on a typo. One per capture process, which
# is the granularity that matters: the logger and the probe write disjoint shard
# trees and must not block each other.
LOGGER = "run_logger"
CFB_PROBE = "cfb_probe"

# Handles are kept here for the life of the process. A Lock that is garbage
# collected closes its descriptor, and closing a descriptor drops the lock - so
# a caller who does not bind the return value would "hold" a lock that silently
# released itself at the next collection.
_held: dict[str, "Lock"] = {}


class AlreadyRunning(RuntimeError):
    """Another live process holds this lock."""

    def __init__(self, name, path, holder):
        self.name, self.path, self.holder = name, path, holder
        who = ""
        if holder:
            who = (f" - held by pid {holder.get('pid')} since "
                   f"{holder.get('started_iso')} [{holder.get('argv')}]")
        super().__init__(f"{name} is already running{who}. Lock file: {path}")


if os.name == "nt":
    import msvcrt

    def _try_lock(fd) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, LOCK_BYTE)
            return True
        except OSError:
            return False

    def _unlock(fd) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, LOCK_BYTE)
        except OSError:
            pass
else:
    import fcntl

    def _try_lock(fd) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


def lock_path(name: str) -> str:
    """Where this lock lives. Derived from config, never a path literal."""
    return config.storage_path("locks", f"{name}.lock")


def holder(name: str) -> dict | None:
    """What the current holder recorded, or None if nothing readable is there.

    Reads from `LOCK_BYTE` so it works while the lock is held on Windows. Never
    raises: this runs on the failure path, where the useful outcome is a name
    and the useless one is a second exception hiding the first.
    """
    try:
        with open(lock_path(name), "rb") as f:
            f.seek(LOCK_BYTE)
            blob = f.read()
    except OSError:
        return None
    try:
        return json.loads(blob.decode("utf-8")) or None
    except (ValueError, UnicodeDecodeError):
        return None


class Lock:
    """A held single-instance lock. Released on `release()` or process exit."""

    def __init__(self, name: str, path: str, fd: int):
        self.name, self.path, self.fd = name, path, fd

    def _stamp(self, argv=None) -> None:
        rec = {
            "pid": os.getpid(),
            "name": self.name,
            "started": time.time(),
            "started_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "argv": " ".join(sys.argv if argv is None else argv),
            "executable": sys.executable,
        }
        blob = json.dumps(rec, separators=(",", ":")).encode("utf-8")
        os.lseek(self.fd, LOCK_BYTE, os.SEEK_SET)
        os.write(self.fd, blob)
        os.ftruncate(self.fd, LOCK_BYTE + len(blob))

    def release(self) -> None:
        if self.fd is None:
            return
        _unlock(self.fd)
        os.close(self.fd)
        self.fd = None
        _held.pop(self.name, None)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()
        return False


def acquire(name: str, argv=None) -> Lock:
    """Take the lock for `name`, or raise `AlreadyRunning`.

    Call this before the first archive write - the resource being protected is
    the shard tree, and a second process that has already appended one record
    has already done the damage.
    """
    path = lock_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    if not _try_lock(fd):
        info = holder(name)          # read BEFORE closing, so the name survives
        os.close(fd)
        raise AlreadyRunning(name, path, info)
    lock = Lock(name, path, fd)
    lock._stamp(argv)
    _held[name] = lock
    return lock
