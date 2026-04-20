"""Cross-platform task runner for pglite-pydb.

Single source of truth for contributor commands. Runs identically on
Linux, macOS, and Windows (PowerShell / cmd / bash). The repo's
``Makefile`` delegates every target here so Linux contributors who keep
using ``make`` get the same behaviour as Windows contributors running
``uv run python tasks.py <name>``.

Usage::

    uv run python tasks.py dev        # full workflow (lint + examples + tests)
    uv run python tasks.py test       # pytest only
    uv run python tasks.py clean      # remove build artefacts (stdlib-only; no rm -rf)
    uv run python tasks.py status     # print environment info
    python tasks.py --list            # list all available tasks

Design notes:
- Uses only the Python standard library (argparse, shutil, subprocess,
  pathlib). No third-party task-runner dependency — see research.md R6.
- ``clean`` uses ``shutil.rmtree`` against a known list of cache paths
  plus ``pathlib.Path.rglob`` for ``__pycache__``. Never shells out to
  ``rm -rf`` or ``find -exec`` (FR-014).
- Each task is a function ``task_<name>(argv: list[str]) -> int``
  returning an exit code. Adding a task = adding a function + a docstring.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent


def _run(cmd: list[str], **kwargs: object) -> int:
    """Run ``cmd``, stream output, return exit code (never raises on non-zero).

    kwargs are passed through to ``subprocess.run``. ``cwd`` defaults to
    REPO_ROOT so relative paths resolve the same on every platform.
    """
    kwargs.setdefault("cwd", str(REPO_ROOT))
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, **kwargs).returncode  # type: ignore[arg-type]  # noqa: S603


def task_dev(argv: list[str]) -> int:
    """Full development workflow: install -> lint -> examples -> tests."""
    for sub in ("install", "lint", "examples", "test"):
        rc = TASKS[sub]([])
        if rc != 0:
            return rc
    return 0


def task_test(argv: list[str]) -> int:
    """Run the pytest suite against ``tests/``."""
    print("Running test suite...", flush=True)
    return _run(["uv", "run", "pytest", "tests/", *argv])


def task_examples(argv: list[str]) -> int:
    """Run the examples/ pytest suite."""
    print("Running examples...", flush=True)
    return _run(["uv", "run", "pytest", "examples/", *argv])


def task_lint(argv: list[str]) -> int:
    """Run linting via pre-commit."""
    print("Running linting checks...", flush=True)
    return _run(["uv", "run", "pre-commit", "run", "--all-files", *argv])


def task_quick(argv: list[str]) -> int:
    """Quick dev-mode smoke: install + lint + import check."""
    for sub in ("install", "lint"):
        rc = TASKS[sub]([])
        if rc != 0:
            return rc
    print("Running quick development checks...", flush=True)
    rc = _run(
        [
            "uv",
            "run",
            "python",
            "-c",
            "import pglite_pydb; "
            "from pglite_pydb import PGliteManager, PGliteConfig; "
            "print('All imports working')",
        ]
    )
    return rc


def task_install(argv: list[str]) -> int:
    """Install project in development mode via ``uv sync``."""
    print("Installing in development mode...", flush=True)
    return _run(["uv", "sync", *argv])


def task_fmt(argv: list[str]) -> int:
    """Auto-fix formatting via ``ruff format``."""
    print("Auto-fixing formatting...", flush=True)
    return _run(["uv", "run", "ruff", "format", *argv])


def task_clean(argv: list[str]) -> int:
    """Remove build artefacts and caches using ``shutil.rmtree`` (no rm -rf).

    Scope: project build output and test caches that live at or near the
    repo root. Uses ``ignore_errors=True`` so missing paths don't fail
    the task.
    """
    print("Cleaning build artifacts...", flush=True)
    # Top-level directories to nuke outright
    top_dirs = [
        REPO_ROOT / "build",
        REPO_ROOT / "dist",
        REPO_ROOT / ".pytest_cache",
        REPO_ROOT / ".mypy_cache",
        REPO_ROOT / ".ruff_cache",
        REPO_ROOT / "htmlcov",
    ]
    for d in top_dirs:
        if d.exists():
            print(f"  rmtree {d.relative_to(REPO_ROOT)}")
            shutil.rmtree(d, ignore_errors=True)

    # Top-level files (coverage output, etc.)
    top_files = [REPO_ROOT / "coverage.xml", REPO_ROOT / ".coverage"]
    for f in top_files:
        if f.exists():
            print(f"  remove {f.relative_to(REPO_ROOT)}")
            f.unlink(missing_ok=True)

    # *.egg-info directories anywhere in the tree
    for egg in REPO_ROOT.rglob("*.egg-info"):
        if egg.is_dir():
            print(f"  rmtree {egg.relative_to(REPO_ROOT)}")
            shutil.rmtree(egg, ignore_errors=True)

    # __pycache__ under src/, tests/, examples/
    for base in ("src", "tests", "examples"):
        base_path = REPO_ROOT / base
        if not base_path.exists():
            continue
        for pyc_dir in base_path.rglob("__pycache__"):
            shutil.rmtree(pyc_dir, ignore_errors=True)

    # Stray .pyc files
    for base in ("src", "tests", "examples"):
        base_path = REPO_ROOT / base
        if not base_path.exists():
            continue
        for pyc_file in base_path.rglob("*.pyc"):
            pyc_file.unlink(missing_ok=True)

    print("Cleanup complete.")
    return 0


def task_status(argv: list[str]) -> int:
    """Print a short environment status line (Python + install state)."""
    print("pglite-pydb project status")
    print("-" * 40)

    rc = _run([sys.executable, "--version"])

    # Try to import and print the installed version
    import_script = (
        "import importlib, sys; "
        "m = importlib.import_module('pglite_pydb'); "
        "print(f'pglite-pydb {m.__version__} ready (installed module: {m.__file__})')"
    )
    rc2 = _run(
        ["uv", "run", "python", "-c", import_script],
        # If import fails we just want a diagnostic, not to surface an error
        check=False,
    )
    if rc2 != 0:
        print("  (pglite-pydb not importable — run `python tasks.py install`)")
    return 0 if rc == 0 else rc


TASKS = {
    "dev": task_dev,
    "test": task_test,
    "examples": task_examples,
    "lint": task_lint,
    "quick": task_quick,
    "install": task_install,
    "fmt": task_fmt,
    "clean": task_clean,
    "status": task_status,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tasks.py",
        description=(
            "Cross-platform task runner for pglite-pydb. See research.md R6 "
            "for why this is plain Python rather than taskipy/invoke/nox."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available tasks and exit.",
    )
    parser.add_argument(
        "task",
        nargs="?",
        choices=sorted(TASKS.keys()),
        help="Task to run.",
    )
    parser.add_argument(
        "task_args",
        nargs=argparse.REMAINDER,
        help="Extra args passed through to the underlying command.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list:
        print("Available tasks:")
        for name, fn in sorted(TASKS.items()):
            doc = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
            print(f"  {name:10s}  {doc}")
        return 0

    if not args.task:
        parser.print_help()
        return 2

    return TASKS[args.task](args.task_args)


if __name__ == "__main__":
    sys.exit(main())
