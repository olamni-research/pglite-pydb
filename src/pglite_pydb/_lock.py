"""Cross-platform advisory instance lock for pglite-pydb data directories.

Implements the `InstanceLock` context manager described in
``specs/003-pglite-path-backup-restore/data-model.md`` §4 and
``specs/003-pglite-path-backup-restore/research.md`` R2.

POSIX:   ``fcntl.flock(fd, LOCK_EX | LOCK_NB)``
Windows: ``msvcrt.locking(fd, LK_NBLCK, 1)`` on byte 0

Non-blocking. Released by the OS on process death — no stale-lock recovery
required.
"""

from __future__ import annotations

import os

from pathlib import Path
from types import TracebackType
from typing import Any

from pglite_pydb._platform import IS_WINDOWS
from pglite_pydb.errors import InstanceInUseError


if IS_WINDOWS:
    import msvcrt  # type: ignore[import-not-found]
else:
    import fcntl  # type: ignore[import-not-found]


SIDECAR_DIRNAME = ".pglite-pydb"
LOCK_FILENAME = "instance.lock"


def lock_path_for(data_dir: Path) -> Path:
    """Return the canonical lock-file path for a resolved data directory."""
    return data_dir / SIDECAR_DIRNAME / LOCK_FILENAME


class InstanceLock:
    """Advisory, non-blocking, process-scoped lock on a PGlite data directory.

    Use as a context manager:

        with InstanceLock(data_dir):
            # exclusive access
            ...
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).resolve(strict=False)
        self.path: Path = lock_path_for(self.data_dir)
        self._fd: int | None = None

    def acquire(self) -> "InstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Touch/open the lock file (read-write, create if missing).
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if IS_WINDOWS:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise InstanceInUseError(self.data_dir) from exc
            else:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (BlockingIOError, OSError) as exc:
                    raise InstanceInUseError(self.data_dir) from exc
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        fd = self._fd
        self._fd = None
        try:
            if IS_WINDOWS:
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> "InstanceLock":
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()

    def __del__(self) -> Any:
        try:
            self.release()
        except Exception:
            pass


__all__ = ["InstanceLock", "lock_path_for", "SIDECAR_DIRNAME", "LOCK_FILENAME"]
