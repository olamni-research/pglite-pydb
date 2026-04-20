# Quickstart: Executing the `pglite-pydb` Port

**Feature**: 001-pglite-pydb-port · **Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md) · **Research**: [research.md](./research.md) · **Data model**: [data-model.md](./data-model.md) · **Contract**: [contracts/public-api.md](./contracts/public-api.md)

This is a reviewer's / executor's runbook. Each of the 12 refactor steps has: **what**, **how (commands)**, **verify**, and **commit**. Every step leaves the Linux suite green before its commit lands.

Assumed baseline: you are on branch `001-pglite-pydb-port`, `uv` and `make` are available, and for steps that need it, a Windows 11 host (or a Windows GitHub Actions runner) is accessible.

---

## Step 1 — Baseline, lockfiles, branch sanity

**What**: prove the pre-refactor state is green and capture the exact pass count so every subsequent step can be compared against it.

**How**:
```bash
git switch 001-pglite-pydb-port               # already done by speckit-git-feature hook
uv sync --all-extras
uv run pytest tests/ -x --tb=short | tee /tmp/baseline-passcount.txt
```

**Verify**: the trailing `X passed` line in `/tmp/baseline-passcount.txt`. Record this number; it is the invariant for SC-002.

**Commit**: none — this is a measurement step. No files changed.

---

## Step 2 — `git mv src/py_pglite src/pglite_pydb`

**What**: atomic directory rename that preserves `git log --follow` history.

**How**:
```bash
git mv src/py_pglite src/pglite_pydb
```

**Verify**:
```bash
git status   # should show 100% renames, zero content changes
```

**Commit**: `[step 2] rename src/py_pglite → src/pglite_pydb (directory only)`. Do **not** touch imports yet.

---

## Step 3 — `pyproject.toml` single-file pivot

**What**: re-point every build/tooling reference from `py_pglite` / `py-pglite` to `pglite_pydb` / `pglite-pydb`.

**How**: edit `pyproject.toml` at these specific lines (line numbers from the current file; confirm with `grep -n`):

- L6 `name = "py-pglite"` → `name = "pglite-pydb"`
- L86, L91–L93, L99: optional-dep references `py-pglite[...]` → `pglite-pydb[...]`
- L130 `py_pglite = "py_pglite.pytest_plugin"` → `pglite_pydb = "pglite_pydb.pytest_plugin"`
- L136 `module-name = "py_pglite"` → `module-name = "pglite_pydb"`
- L164 `module = "py_pglite.django.*"` → `module = "pglite_pydb.django.*"` (mypy override)
- L218 `known-first-party = ["py_pglite"]` → `known-first-party = ["pglite_pydb"]`
- L230 `source = ["src/py_pglite"]` → `source = ["src/pglite_pydb"]` (coverage)

**Verify**:
```bash
uv sync --reinstall            # must succeed; wheel internal path now matches src/pglite_pydb
rg -nw '"py-pglite"|"py_pglite"' pyproject.toml    # 0 hits
```

**Commit**: `[step 3] pyproject.toml: rename build metadata to pglite-pydb`

---

## Step 4 — Mass import rename across Python sources

**What**: whole-word rewrite of every `py_pglite` reference in Python code.

**How**:
```bash
# Preview the change set
rg -n "\\bpy_pglite\\b" src/ tests/ examples/ conftest.py 2>/dev/null | wc -l

# Apply (use any tool — sed, ruff, find + sd, IDE refactor). One option:
#   - from py_pglite        → from pglite_pydb
#   - import py_pglite      → import pglite_pydb
#   - py_pglite.<x>         → pglite_pydb.<x>  (whole-word, case-sensitive)
# IMPORTANT: do NOT rewrite the README deprecation note if already added;
# for this step, there is no deprecation note yet (Step 5 adds it).

uv sync --reinstall
uv run pytest tests/ -x --tb=short
```

**Verify**:
```bash
rg -nw "py_pglite" --glob "*.py"     # 0 hits
# Linux test count matches Step 1 baseline
```

**Commit**: `[step 4] mass-update imports: py_pglite → pglite_pydb`

---

## Step 5 — Docs, CI, Makefile text refs (preliminary pass)

**What**: update user-facing text references to the new name. `Makefile` still works on Linux; its cross-platform replacement is Step 10. CI Windows matrix is Step 11; this step only renames strings.

**How**: targeted edits in:

- `README.md` (install commands, import examples, badge URLs): `py-pglite` → `pglite-pydb`, `py_pglite` → `pglite_pydb`. Add the deprecation note from R9 of `research.md` immediately after the project description.
- `CONTRIBUTING.md`: same find/replace.
- `.safety-project.ini`: project name.
- `.github/workflows/ci.yml` L56–L57: the `import py_pglite` smoke check.

**Verify**:
```bash
rg -nw "py_pglite|py-pglite"   # returns exactly 1 hit = the intentional deprecation note in README.md
uv run pytest tests/ -x --tb=short   # Linux still green
```

**Commit**: `[step 5] rename text references in README, CONTRIBUTING, CI, safety config`

---

## Step 6 — `_platform.py` utility

**What**: the one place where `sys.platform` is read.

**How**: create `src/pglite_pydb/_platform.py`:

```python
"""Centralised platform detection.

All sys.platform / os.name checks in pglite_pydb MUST import from here.
Adding a new platform branch anywhere else is a code-review red flag.
"""
import sys

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

# AF_UNIX exists on Windows 10+, but PGlite's Node server writes socket paths
# that Windows psycopg cannot portably address. TCP loopback is the Windows
# transport for this project. See research.md R2.
SUPPORTS_UNIX_SOCKETS = not IS_WINDOWS
```

**Verify**:
```bash
uv run python -c "from pglite_pydb._platform import IS_WINDOWS, IS_LINUX, IS_MACOS, SUPPORTS_UNIX_SOCKETS; print(IS_LINUX)"
uv run pytest tests/ -x --tb=short
```

**Commit**: `[step 6] add src/pglite_pydb/_platform.py`

---

## Step 7 — Windows TCP auto-select in `config.py`

**What**: `PGliteConfig.__post_init__` auto-promotes to TCP on Windows unless the user has explicitly set `use_tcp`.

**How**: edit `src/pglite_pydb/config.py` `__post_init__` (currently lines 58–83). The transition table is in `data-model.md` Entity 4. Detecting "user did not set `use_tcp`" requires capturing a sentinel — the simplest approach is to add a private `_use_tcp_explicit: bool = False` flag that the `PGliteConfig.__init__` override sets when the user passes `use_tcp` (or: change the field default to an `_UNSET` sentinel and promote inside `__post_init__`).

Must raise `RuntimeError` when the user explicitly sets `use_tcp=False` on Windows, with a message naming the platform (FR-005).

On Windows auto-promotion, also set `tcp_port = 0` (OS-assigned ephemeral) unless the user set it explicitly — aligns with R4.

**Verify**:
```bash
uv run pytest tests/ -x --tb=short   # Linux: behaviour unchanged; defaults still socket-based
# Windows (spike, not CI yet):
powershell -c "uv run python -c 'from pglite_pydb import PGliteConfig; c = PGliteConfig(); print(c.use_tcp, c.tcp_port)'"
# Expected on Windows: True 0
```

**Commit**: `[step 7] PGliteConfig: auto-select TCP on Windows`

---

## Step 8 — `_resolve_node_bin` helper

**What**: stop passing bare `"npm"` / `"node"` to `subprocess.Popen`.

**How**: in `src/pglite_pydb/manager.py`:

- Add the helper from R5 of `research.md` (private module-level function).
- Replace L346 `["npm", "install"]` → `[_resolve_node_bin("npm"), "install"]`.
- Replace L393 `["node", "pglite_manager.js"]` → `[_resolve_node_bin("node"), "pglite_manager.js"]`.
- Leave `shell=False` (current state) — `shutil.which` returns the absolute path; `shell=True` is never needed.

**Verify**:
```bash
uv run pytest tests/ -x --tb=short   # Linux green

# Windows spike (Python 3.12, Node 22):
powershell -c "uv run python -c 'from pglite_pydb.manager import _resolve_node_bin; print(_resolve_node_bin(\"node\"))'"
# Expected: absolute path to node.exe, e.g. C:\Program Files\nodejs\node.exe
```

**Commit**: `[step 8] manager: resolve node/npm via shutil.which with .cmd/.exe fallback`

---

## Step 9 — `_terminate_process_tree` helper

**What**: cross-platform graceful-then-forceful shutdown of the Node subprocess and all its descendants.

**How**: in `src/pglite_pydb/manager.py`:

- Extract the termination logic currently at L507–L531 into `_terminate_process_tree(proc, timeout=5.0)`.
- Branch on `IS_WINDOWS`:
  - POSIX path (unchanged semantics): `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` → `proc.wait(timeout)` → `os.killpg(..., signal.SIGKILL)` on timeout.
  - Windows path: `psutil.Process(proc.pid).children(recursive=True)` + the parent → `.terminate()` each → `proc.wait(timeout)` → `.kill()` any `is_running()` survivors.
- Keep `preexec_fn=os.setsid` at L400–L402 guarded by `hasattr(os, "setsid")` (already correct; annotate why for future readers).

**Verify**:
```bash
uv run pytest tests/ -x --tb=short   # Linux: unchanged (POSIX path still taken)

# 100-cycle orphan check (Windows spike, informs SC-003):
powershell -c "1..100 | % { uv run pytest tests/test_smoke.py -q }"
powershell -c "Get-Process node -ErrorAction SilentlyContinue"   # expected: no output (no orphans)
```

**Commit**: `[step 9] manager: cross-platform process-tree termination`

---

## Step 10 — Python task runner + Makefile delegation

**What**: canonical task logic lives in `tasks.py`; `Makefile` becomes a thin wrapper (per Q1 clarification, Option B).

**How**:

1. Create `tasks.py` at repo root (stdlib only, `argparse` dispatcher). Each subcommand is a function with the signature `def task_<name>(argv: list[str]) -> int`. Tasks to implement: `dev`, `test`, `examples`, `lint`, `quick`, `install`, `fmt`, `clean`, `status`. `clean` MUST use `shutil.rmtree(..., ignore_errors=True)` against a list of cache paths — no `rm -rf` / `find -exec`.
2. Edit `Makefile` — each existing target becomes a single-line delegation:
   ```make
   test:
       uv run python tasks.py test
   ```
   Preserve the `.PHONY:` declarations.
3. Document the Windows invocation in `CONTRIBUTING.md`: `uv run python tasks.py <name>` (or the equivalent `py -3 tasks.py <name>`).

**Verify**:
```bash
# Linux: behaviour unchanged
make test
# Windows PowerShell
uv run python tasks.py test
```

**Commit**: `[step 10] tasks.py cross-platform task runner; Makefile delegates`

---

## Step 11 — CI matrix: add Windows, expand Node

**What**: `.github/workflows/ci.yml` — 2-OS × 5-Python matrix, Node 20 LTS smoke cells, xdist invocation.

**How**: apply the matrix shape from R11 of `research.md`. Also:

- Replace the L56–L57 smoke-check script (currently `python -c "import py_pglite; print(py_pglite.__version__)"`) with its `pglite_pydb` equivalent.
- Ensure inline shell steps run under `pwsh` on Windows (`shell: pwsh`) or use the default (which is already `pwsh` on `windows-latest`, `bash` on `ubuntu-latest` — GitHub's default behaviour is adequate for most steps).
- Guard Unix-socket-only tests with `@pytest.mark.skipif(IS_WINDOWS, reason="Unix socket transport")`; sweep `tests/` once and tag them.

**Verify**: open the PR, watch the Actions tab. All 12 cells plus xdist cells must pass.

**Commit**: `[step 11] CI: add windows-latest matrix, Node 20 smoke, xdist`

---

## Step 12 — Release verification & version bump

**What**: final prove-out; bump version to `0.6.0`; tag.

**How**:

1. Bump `version` in `pyproject.toml` to `0.6.0`.
2. `uv build` — produces `dist/pglite_pydb-0.6.0-*.whl` and `dist/pglite_pydb-0.6.0.tar.gz`.
3. In a fresh venv on Windows PowerShell:
   ```powershell
   python -m venv .venv-verify
   .\.venv-verify\Scripts\Activate.ps1
   pip install (Resolve-Path .\dist\pglite_pydb-0.6.0-*.whl)[all]
   pytest examples\quickstart\
   ```
4. In a fresh venv on Linux:
   ```bash
   python -m venv .venv-verify
   source .venv-verify/bin/activate
   pip install "dist/pglite_pydb-0.6.0-py3-none-any.whl[all]"
   pytest examples/quickstart/
   ```
5. Final grep check:
   ```bash
   rg -nw "py_pglite|py-pglite"   # expected: exactly 1 hit (the deprecation note in README.md)
   ```
6. Tag:
   ```bash
   git tag -a v0.6.0 -m "pglite-pydb 0.6.0: renamed from py-pglite, cross-platform (Linux + Windows)"
   git push origin v0.6.0
   ```

**Verify**: the contract tests from [`contracts/public-api.md`](./contracts/public-api.md) §8 all pass on both OSes; `pip show pglite-pydb` reports `Name: pglite-pydb`; `pytest --trace-config` lists `pglite_pydb`.

**Commit**: `[step 12] release 0.6.0: cross-platform verification`

---

## After Step 12

The branch is ready to open a PR against `main`. Suggested PR title: **"Rename py-pglite → pglite-pydb; add Windows/PowerShell support"**. The PR description should link to `spec.md`, `plan.md`, and list the 12 commits with one-line descriptions (they will match the commit messages above).

## Abort / rollback guidance

- **Step 1–5 revert**: `git reset --hard <pre-refactor-sha>` is safe; nothing external has been published.
- **Step 6–10 revert**: same as above — the platform helpers are additive, so reverting the whole branch is clean.
- **Step 11 revert**: if the Windows matrix reveals a blocker that cannot be fixed in-branch, revert just the CI edit, keep the code changes, and ship 0.6.0 as Linux-only. Windows would then be a follow-up `0.7.0`.
- **Step 12 revert (post-tag)**: `git tag -d v0.6.0` locally; `git push --delete origin v0.6.0` remotely (destructive — confirm with maintainer). Yank the wheel from PyPI if already uploaded (`uv publish` has not been run yet at this point in the plan — publication is a separate out-of-band action).
