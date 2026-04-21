"""Backup and restore engine for pglite-pydb instances.

Phase 1 placeholder. Real implementation lands in Phase 4 (US2) and
Phase 5 (US3) per `specs/003-pglite-path-backup-restore/tasks.md`.

TODO(T022..): BackupEngine class with:
  - create_logical(instance, selection, *, force_hot=False)
  - create_full_snapshot(instance)
  - restore_logical(instance, containers, *, overwrite=False, assume_yes=False)
  - restore_full_snapshot(instance, container, *, assume_yes=False,
                          assume_yes_destroy=False)
"""
