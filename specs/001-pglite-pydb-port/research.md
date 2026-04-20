# Phase 0 Research: pglite-pydb Port

**Feature**: 001-pglite-pydb-port · **Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)

This document resolves every NEEDS CLARIFICATION raised in the plan's Technical Context and captures the behavioural research that informs each platform-sensitive refactor step.

## R1. Python-matrix discrepancy — CI vs. spec

**Decision**: Extend CI's Python matrix to `["3.10", "3.11", "3.12", "3.13", "3.14"]` as part of Step 11 of the refactor.

**Rationale**: The spec (FR-011) and SC-001 both assume Python 3.14 is supported. The current `.github/workflows/ci.yml` (line 15) only covers `["3.10", "3.11", "3.12", "3.13"]`. Python 3.14 is generally available (January 2026). The refactor already edits this file, so adding 3.14 is a one-character list extension — refusing to would make the spec's own success criteria unverifiable.

**Alternatives considered**:
- *Restrict spec to 3.10–3.13*: would re-open the clarification session. Rejected because Python 3.14 is out and the port is explicitly forward-looking.
- *Add 3.14 in a separate follow-up PR*: would split the CI edit across two PRs for no benefit. Rejected.

## R2. Unix-domain sockets on Windows

**Decision**: Do **not** use Unix-domain sockets on Windows; force TCP transport in `PGliteConfig.__post_init__` when `sys.platform == "win32"` and `use_tcp` was not explicitly provided by the user.

**Rationale**: Windows 10 (build 17063+) and Windows 11 support `AF_UNIX`, but:
1. The PGlite Node server writes its socket path into a Unix-style path that the Windows `psycopg` client cannot resolve portably (the `host=` field expects a path that the Windows driver can `connect()` to, and the Node and Python sides disagree on path semantics).
2. Windows socket-file lifetime rules differ (no auto-unlink on bind), which complicates teardown without giving any capability that TCP loopback doesn't already provide.
3. TCP loopback on `127.0.0.1` is a first-class transport on every OS and incurs negligible overhead for a test fixture.

**Alternatives considered**:
- *Native Windows AF_UNIX*: theoretically possible but would require patching both the Node-side path generation and the Python-side driver configuration, with no benefit over TCP. Rejected.
- *Named pipes (Windows-specific)*: psycopg does not support named-pipe transport. Rejected.

**Behaviour matrix** (informs Step 7):

| OS         | `use_tcp` default | Override behaviour                                          |
|------------|-------------------|-------------------------------------------------------------|
| Linux      | `False` (socket)  | user may set `use_tcp=True` → TCP on `127.0.0.1:<port>`     |
| macOS      | `False` (socket)  | same as Linux                                               |
| Windows    | `True` (TCP)      | explicit `use_tcp=False` → `RuntimeError` with remediation hint |

## R3. Process-tree termination on Windows

**Decision**: Introduce a `_terminate_process_tree(proc)` helper in `src/pglite_pydb/manager.py` that branches on `IS_WINDOWS`:

- **Linux / macOS (existing path, preserved)**: `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` → `proc.wait(timeout=N)` → on timeout, `os.killpg(pgid, signal.SIGKILL)`.
- **Windows (new path)**: `psutil.Process(proc.pid)` → collect `children(recursive=True)` → call `.terminate()` on parent + all children → `proc.wait(timeout=N)` → on timeout, call `.kill()` on any `is_running()` survivors.

**Rationale**: The existing POSIX path relies on `os.killpg` (sending a signal to every member of a process group), which has no Windows equivalent. `psutil` is already a declared dependency (`pyproject.toml` L40–L42), so no new package is introduced. Process-tree enumeration on Windows is the idiomatic replacement.

**Bounded timeout**: 5 seconds for graceful, then forceful kill. Matches the current implicit expectation in the POSIX path (which also uses a timeout between SIGTERM and SIGKILL).

**Alternatives considered**:
- *Windows Job Objects* (via `pywin32`): more powerful (guarantees descendant cleanup on parent exit) but adds a mandatory Windows-only dependency. Rejected — `psutil` is sufficient and cross-platform.
- *Rely on Node's own SIGINT/SIGTERM handler*: Node *does* handle both on Windows (normalised), but the handler only cleans the Node process — not any grandchildren the server might have spawned (e.g. `npm` during install). We still need the tree walk for the `npm install` step.

## R4. `pytest-xdist` port allocation

**Decision**: Bind to TCP port `0` and let the OS assign a free ephemeral port; record the assigned port on the `PGliteManager` instance for connection-string construction.

**Rationale**: The `pytest-xdist` worker IDs (`gw0`, `gw1`, …) could be used to derive a port (`base + worker_id`), but:
1. OS port-0 assignment is race-free by construction (`bind()` atomically reserves the port against concurrent binds).
2. Worker-ID derivation requires a base port that doesn't collide with anything else on the developer's machine — fragile.
3. The Node-side PGlite server can be launched with `--port 0` (or equivalent CLI flag in the generated `pglite_manager.js`) and echo the chosen port back on stdout, which the Python side already parses for socket readiness.

**Concrete contract** (informs Step 7 and FR-011a):
- `PGliteConfig.tcp_port == 0` means "let the OS choose".
- The resolved port is exposed on `PGliteManager.port` after `start()` returns.
- Connection strings built by fixtures read from `manager.port`, not from `config.tcp_port`.

**Alternatives considered**:
- *Worker-ID-derived static port*: rejected for fragility (see above).
- *File-lock port registry*: overengineered for the problem. Rejected.

## R5. `shutil.which` resolution for Node/npm on Windows

**Decision**: `_resolve_node_bin(name: str) -> str` (private helper in `src/pglite_pydb/manager.py`) resolves via `shutil.which` with Windows fallbacks:

```python
def _resolve_node_bin(name: str) -> str:
    candidates = [name] if not IS_WINDOWS else [name, f"{name}.cmd", f"{name}.exe"]
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise FileNotFoundError(
        f"Could not locate '{name}' on PATH. Searched: {candidates}. "
        f"Install Node.js 20 LTS or 22 LTS from https://nodejs.org/"
    )
```

**Rationale**:
- On Windows, `npm` ships as `npm.cmd` (batch) and `node` as `node.exe`; bare `"npm"` or `"node"` strings passed to `subprocess.Popen` with `shell=False` fail with `FileNotFoundError` because Windows does not automatically resolve extension-less executables.
- `shutil.which` respects `PATHEXT`, so on Windows calling it with `"npm"` usually finds `npm.cmd` anyway. The explicit `.cmd` / `.exe` fallback is defensive — some environments strip `PATHEXT` to only `.EXE`.
- Returning the resolved absolute path and passing it to `subprocess.Popen([...], shell=False)` sidesteps PowerShell execution-policy prompts that would trigger on `.ps1` wrappers (critical for unattended CI runs).

**Alternatives considered**:
- *`shell=True`*: would let Windows resolve the binary name but exposes us to shell-injection and PowerShell execution-policy prompts. Rejected on security and ergonomics grounds.
- *Hard-coded Node installation path*: not portable. Rejected.

## R6. Cross-platform Python task runner — library choice

**Decision**: Use a plain hand-written `tasks.py` with an `argparse`-based dispatcher (no third-party task-runner library).

**Rationale**:
- The clarification (Q1) asked for a Python task runner as the canonical source of truth — it did not specify a library.
- The 9 tasks (`dev`, `test`, `examples`, `lint`, `quick`, `install`, `fmt`, `clean`, `status`) are thin wrappers around `uv run …` / `ruff …` / `shutil.rmtree …` — none require a framework's features (dependency graphs, file-change detection, caching).
- Adding `taskipy`, `invoke`, `nox`, or `tox` introduces a new dependency that every contributor must install (or that must be pinned in `pyproject.toml`). A 120-line `tasks.py` has zero dependencies beyond stdlib.
- `tasks.py` is trivially callable from the `Makefile` (`uv run python tasks.py <name>`) and from PowerShell (`uv run python tasks.py <name>`) — one invocation, identical on every OS.

**Alternatives considered**:
- *`taskipy`*: would live inside `pyproject.toml`, nice integration with `uv run task <name>`. Rejected — adds a dependency and less discoverable than a visible `tasks.py`.
- *`invoke`*: powerful, but over-engineered for 9 shell-out-shaped tasks. Rejected.
- *`nox`*: excellent for test-matrix automation but overlaps with `pytest` + CI; not justified by the 9 tasks. Rejected.

## R7. Python-version floor and Node-version floor

**Decision**:
- **Python**: 3.10 (floor, unchanged) through 3.14 (ceiling). Matches current CI after R1.
- **Node**: 20 LTS (floor, per Q4 clarification) and 22 LTS (ceiling).

**Rationale**: Both floors correspond to currently-supported upstream LTS lines. `pyproject.toml` `requires-python` stays at its current `>=3.10` setting (no change needed on the floor). Node has no `engines` constraint in `package.json` currently; we will add `"engines": { "node": ">=20" }` to reflect the new floor but will not enforce strict bounds (npm warns but does not block).

**Alternatives considered**: covered in the Q4 clarification.

## R8. Preservation of the SQL-injection patch

**Decision**: The existing Django patch from commit `31bc3a2` ("Upgrade Django from 5.2.4 to 5.2.8 to fix SQL injection and DoS vulnerabilities") is preserved by construction — the refactor only edits import paths and string literals, not the `django/` subpackage's vulnerability-relevant code.

**Verification approach**: Step 12 of the refactor includes running `safety` (already in CI `security` job) against the renamed distribution; the patch's presence is covered by the unchanged dependency pin on `Django>=5.2.8` in `pyproject.toml`.

**Rationale**: FR-017 requires the patch to survive. A rename cannot reintroduce a Django-side vulnerability, so this is more of an assertion than a decision, but is worth logging explicitly so `/speckit.analyze` does not flag it as unresolved.

## R9. Deprecation pointer in `README.md`

**Decision**: A single note at the top of `README.md`, immediately after the project description, formatted as:

> **Note:** This project was previously published under the name `py-pglite`. The old PyPI distribution (`py-pglite==0.5.3`) is intentionally frozen and receives no further updates. New installations should use `pip install pglite-pydb`; existing projects must update imports from `py_pglite` to `pglite_pydb` (no backward-compatibility alias is provided).

**Rationale**: Satisfies FR-013 ("single deprecation note") and encodes the Q2 (hard rename) + Q5 (frozen legacy PyPI release) decisions in one place. The note's phrasing is prescriptive about the absence of a shim so users running `pip install -U py-pglite` understand why they don't see an update.

## R10. Refactor step ordering — critical path revalidated

**Decision**: Execute steps in the order the feature-spec outline already dictates (1→2→3→4→5→6→7→8→9→10→11→12), not the suggested re-ordering in the original 12-step plan's footnote ("Step 1 → 2 → 4 → 3 → 5").

**Rationale**: The footnote proposed running mass import rename (Step 4) before `pyproject.toml` edits (Step 3). In practice, `pyproject.toml` is a single file whose edits take 2 minutes; mass import rename touches ~50 files and is irreversibly noisy if the build metadata isn't aligned. The safer order:
1. Baseline (Step 1).
2. Directory rename via `git mv` (Step 2) — atomic, clean history.
3. Update `pyproject.toml` (Step 3) — re-points build to the new directory; `uv sync --reinstall` validates this before imports are touched.
4. Mass-update imports (Step 4) — now runs against a correctly-configured build.

Step 3 before Step 4 avoids a failure mode where `uv sync` succeeds (reinstalling from the old pinned metadata), imports appear to work, but the installed wheel's internal layout has drifted from source.

**Alternatives considered**: the footnote's 1→2→4→3 ordering. Rejected for the metadata-drift risk above.

## R11. CI matrix shape (final)

**Decision** (informs Step 11):

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, windows-latest]
    python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]
    node-version: ["22"]
    include:
      - os: ubuntu-latest
        python-version: "3.12"
        node-version: "20"        # Node 20 LTS smoke cell on Linux
      - os: windows-latest
        python-version: "3.12"
        node-version: "20"        # Node 20 LTS smoke cell on Windows
  fail-fast: false
```

**Rationale**: 2 × 5 × 1 = 10 regular cells + 2 Node-20 smoke cells = 12 total test-job cells. Each OS/Python combination runs once with Node 22 (matching current behaviour on Linux), and one cell per OS proves Node 20 LTS compatibility without exploding the matrix to 2 × 5 × 2 = 20.

**xdist cell (per FR-011)**: add a second test invocation within each matrix cell: `pytest -n auto` after the serial run. Single `.github/workflows/ci.yml` step with `continue-on-error: false` so xdist regressions block merges.

## Outstanding NEEDS CLARIFICATION

**None.** All items flagged in the plan's Technical Context are resolved here. The plan's Constitution Check is N/A (unratified constitution), which is a state-of-the-world observation rather than an open question.
