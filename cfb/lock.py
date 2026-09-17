"""A single-instance lock that the operating system releases.

Two processes appending to one CFB raw shard destroyed 43 of 114 of them on
2026-09-11. A PID file would not have stopped it: a stale PID survives a crash,
and on Windows `os.kill(pid, 0)` does not probe a process, it TERMINATES it.
So this takes a byte-range lock on an open file handle. The kernel drops it
when the process dies, however it dies, and a second instance fails at once
rather than waiting.
"""
import os

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class AlreadyRunning(Exception):
    pass


class InstanceLock:
    def __init__(self, path):
        self.path = path
        self._f = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        f = open(self.path, "a+b")
        try:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise AlreadyRunning(f"another CFB ingest holds {self.path}")
        self._f = f
        return self

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
        finally:
            self._f.close()
            self._f = None
        return False
