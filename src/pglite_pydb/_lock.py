"""Cross-platform advisory instance lock for pglite-pydb data directories.

Phase 1 placeholder. Real implementation (fcntl.flock on POSIX,
msvcrt.locking on Windows) lands in T007 per
`specs/003-pglite-path-backup-restore/tasks.md`.

TODO(T010): InstanceLock context manager at
`<data-dir>/.pglite-pydb/instance.lock`; raises InstanceInUseError on
contention; releases on __exit__ and on process death (OS-level).
"""
