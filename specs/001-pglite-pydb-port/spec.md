# Feature Specification: Port `py_pglite` to `pglite-pydb` with Cross-Platform (Linux + Windows/PowerShell) Support

**Feature Branch**: `001-pglite-pydb-port`
**Created**: 2026-04-20
**Status**: Draft
**Input**: User description: "Perform an in-depth analysis of the py_pglite source code in this pglite-pydb repo and create a 12-step detailed and executable refactoring plan to port py_pglite into its new name pglite-pydb and enable it to run on Windows in PowerShell. The port should also allow pglite-pydb to continue running under Linux."

## Clarifications

### Session 2026-04-20

- Q: Makefile / task-runner strategy for the 9 contributor tasks on Windows and Linux → A: Keep the Makefile for Linux/macOS and add a cross-platform Python task runner (single source of truth in Python; Makefile becomes a thin wrapper).
- Q: Backward-compatibility import shim for the old `py_pglite` name → A: Hard rename, no shim. Users must update every `py_pglite` import to `pglite_pydb` on upgrade; no runtime alias, no DeprecationWarning layer, no permanent dual-name.
- Q: `pytest-xdist` parallelism support on Windows → A: First-class. The fixture MUST handle per-worker TCP port allocation without collisions, and CI MUST exercise `-n auto` on both `windows-latest` and `ubuntu-latest`.
- Q: Supported Node.js versions on Windows (and overall) → A: Node 20 LTS and Node 22 LTS (both LTS lines). CI runs Node 22 across every matrix cell plus one Node 20 smoke cell per OS. Node 24 (Current / odd-numbered) is not supported.
- Q: Legacy `py-pglite` distribution disposition on PyPI → A: Do nothing. The old `py-pglite` 0.5.3 release stays as-is on PyPI; no final deprecation release, no stub package, no yanks. Users who run `pip install py-pglite` continue to receive the old version silently; migration is driven solely by the README deprecation pointer on the new repo.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Windows/PowerShell developer installs and runs the test harness (Priority: P1)

A Python developer on Windows 11 who uses PowerShell as their default shell wants to add an in-process PostgreSQL-compatible test database to their project. They install the package, open PowerShell, and run their existing pytest suite. The fixture spins up a PGlite-backed database, their tests connect successfully over TCP, and the fixture tears the database down cleanly at session end — no manual Unix-socket configuration, no leftover Node processes, and no PowerShell execution-policy prompts.

**Why this priority**: This is the entire reason for the port. Windows developer enablement is the net-new capability; everything else in the refactor supports it. If this story does not work, the port has failed.

**Independent Test**: On a clean Windows 11 VM with Python 3.12 and Node 22 LTS installed, run `pip install pglite-pydb[all]` in a fresh virtualenv inside PowerShell, then execute a minimal pytest file that uses the `pglite_engine` fixture to `SELECT 1`. The test must pass, the fixture must release its TCP port, and no `node.exe` process must remain after the session. The same test must also pass on an identical host running Node 20 LTS instead of Node 22.

**Acceptance Scenarios**:

1. **Given** a clean Windows 11 host with Python 3.10–3.14 and either Node 20 LTS or Node 22 LTS installed, **When** the developer runs `pip install pglite-pydb[all]` in PowerShell, **Then** installation completes without requiring Visual C++ Build Tools, WSL, or admin privileges.
2. **Given** the package is installed on Windows, **When** the developer runs `pytest` on a suite that uses the default `pglite_engine` fixture without any explicit configuration, **Then** the fixture auto-selects TCP transport, spawns the PGlite Node process, accepts connections on an ephemeral port, and all tests run to completion.
3. **Given** a pytest session using the fixture has just finished on Windows, **When** the developer inspects running processes, **Then** no orphaned `node.exe` or `npm.cmd` child processes remain, and the ephemeral TCP port is released.
4. **Given** a developer on Windows explicitly sets `use_tcp=False` in `PGliteConfig`, **When** the fixture initialises, **Then** the system raises a clear error that names the platform, explains that Unix-socket transport is unsupported on Windows, and suggests the default TCP path.
5. **Given** Node.js is not installed on the Windows host, **When** the fixture attempts to start, **Then** the system raises an error that lists exactly which binary names were searched (e.g. `node`, `node.exe`, `npm`, `npm.cmd`) and points at the installation instructions.

---

### User Story 2 — Existing Linux/macOS user upgrades without breakage (Priority: P1)

A developer who has been running `py_pglite` on Linux (the current user base) upgrades to the renamed `pglite-pydb`. Their existing fixtures, SQLAlchemy models, Django integrations, and Unix-socket-based workflow must continue to work. The only required change is the import statement; everything else — performance, defaults, fixture names, configuration shape — must be preserved.

**Why this priority**: The port must not regress the existing platform. Linux is the production platform for the current user base, and silently changing defaults (for instance, forcing TCP on Linux) would break real workloads.

**Independent Test**: On Linux, check out the refactored branch, install with `uv sync --all-extras`, and run the full existing test suite (`uv run pytest tests/`). Baseline pass count captured at the start of the refactor must match after every step. In addition, run the Django and SQLAlchemy example projects unmodified except for import lines — both must pass.

**Acceptance Scenarios**:

1. **Given** the Linux test suite passed with N tests before the refactor began, **When** the full refactor is complete and `uv run pytest tests/` runs on the same host, **Then** exactly N tests pass (no regressions; skipped count unchanged on Linux).
2. **Given** a Linux user's existing project with `from py_pglite import PGliteManager`, **When** they update only the import to `from pglite_pydb import PGliteManager` and reinstall, **Then** their code runs identically with no other changes required.
3. **Given** the default fixture configuration on Linux, **When** the fixture starts, **Then** it uses a Unix domain socket (not TCP), identical to the pre-refactor behaviour.
4. **Given** a Linux user who had custom `PGliteConfig(use_tcp=True, port=54321)`, **When** they reinstall the renamed package, **Then** that configuration continues to work with identical semantics.

---

### User Story 3 — CI pipeline covers both platforms (Priority: P2)

The project's GitHub Actions pipeline currently runs only on Ubuntu. Maintainers need the pipeline to cover both `ubuntu-latest` and `windows-latest` across the supported Python matrix, so that Windows regressions are caught at PR time rather than reported by users. Tests that are legitimately Unix-socket-only must be skipped on Windows with a reason, not deleted.

**Why this priority**: Without CI enforcement, Windows support will silently bit-rot as the project evolves. Blocks long-term maintainability rather than initial release, so P2 not P1.

**Independent Test**: Open a PR against the refactor branch that introduces a deliberately Windows-breaking change (e.g. hard-coding `/tmp` as a socket path). The CI run must fail on `windows-latest` while passing on `ubuntu-latest`, demonstrating that Windows coverage is real and not merely green-by-default.

**Acceptance Scenarios**:

1. **Given** the updated CI workflow, **When** a pull request is opened, **Then** jobs run on both `ubuntu-latest` and `windows-latest` across Python 3.10, 3.11, 3.12, 3.13, and 3.14.
2. **Given** a test that is marked `skipif(IS_WINDOWS, reason="Unix socket transport")`, **When** CI runs on Windows, **Then** that test is recorded as skipped with the stated reason (not as a pass, not as a failure).
3. **Given** inline shell steps in the workflow, **When** running on Windows, **Then** they execute under `pwsh`, not `bash`, and succeed.

---

### User Story 4 — Distribution metadata matches the new name end-to-end (Priority: P2)

A user installs the package, then inspects it with `pip show`, imports it, and builds a wheel. Every surface — distribution name, import name, entry-point name, documentation, CI badges, and the wheel file itself — must consistently say `pglite-pydb` / `pglite_pydb`. No residual `py_pglite` / `py-pglite` references remain, except for one intentional deprecation note that points the old name at the new one.

**Why this priority**: Inconsistent naming causes real breakage — pytest plugin auto-discovery fails if the entry-point name drifts, and published documentation that says `pip install py-pglite` sends users to the wrong package. Important for correctness but not blocking of the Windows enablement story.

**Independent Test**: After the refactor, run `uv build`, then in a fresh virtualenv `pip install dist/pglite_pydb-*.whl[all]` and `pip show pglite-pydb`. The shown `Name` must be `pglite-pydb`, `Location` must contain `pglite_pydb`, and `pytest --trace-config` must list `pglite_pydb` as a registered plugin. Additionally, `rg -w "py_pglite|py-pglite"` across tracked files must return zero hits apart from the deprecation note.

**Acceptance Scenarios**:

1. **Given** the completed refactor, **When** `uv build` runs, **Then** the wheel filename begins with `pglite_pydb-` and the sdist filename begins with `pglite_pydb-`.
2. **Given** the wheel is installed in a fresh virtualenv, **When** `pytest --trace-config` is run, **Then** the output lists `pglite_pydb` as a plugin (and does not list `py_pglite`).
3. **Given** the full repository tree (excluding `node_modules/`, `.git/`, and the deprecation note in `README.md`), **When** searched for the whole words `py_pglite` or `py-pglite`, **Then** no matches are returned.
4. **Given** the published `README.md`, **When** a reader scans for installation instructions, **Then** all `pip install` and import examples reference `pglite-pydb` / `pglite_pydb`.

---

### User Story 5 — Contributor can run every make-style task on Windows (Priority: P3)

A new contributor on Windows clones the repository and wants to run the same developer workflows that Linux contributors use — setup, test, lint, format, clean, status. They invoke a cross-platform Python task runner (e.g. `uv run task <name>`) that works identically on every supported OS. Linux contributors keep using `make <target>`, which now delegates to the same Python task logic.

**Why this priority**: Contributor ergonomics matter, but unlike the end-user install path this affects only maintainers and contributors. P3 because it can ship after the core Windows enablement work and does not block the user-facing release.

**Independent Test**: On Windows PowerShell, invoke the Python task runner to execute `dev`, `test`, `examples`, `lint`, `quick`, `install`, `fmt`, `clean`, and `status`. All must succeed without editing any file. On Linux, invoke the same task names via `make` and observe identical behaviour (because both dispatch to the same Python task logic).

**Acceptance Scenarios**:

1. **Given** a fresh clone on Windows, **When** a contributor runs the task-runner `dev` target, **Then** `uv sync --all-extras` completes and the test suite runs.
2. **Given** the `clean` task is invoked on Windows via the Python task runner, **When** it runs, **Then** build artefacts and caches are removed using a Python helper (no reliance on `rm -rf` or `find -exec`).
3. **Given** the `Makefile` is retained on Linux, **When** a Linux contributor runs `make <target>`, **Then** it delegates to the same Python task logic that Windows invokes directly — there is one source of truth for each task's behaviour.
4. **Given** a maintainer adds a new task, **When** they add it once in the Python task runner, **Then** it is automatically available on both Windows (via `uv run task <name>`) and Linux (via `make <name>`) without duplication.

---

### Edge Cases

- **Port already in use on Windows**: the fixture must either retry with a different ephemeral port or fail with a clear error; it must not silently hang.
- **Node process wedges and does not respond to `terminate()`**: the Windows termination path must escalate from `terminate()` to `kill()` on any surviving descendants after a bounded timeout.
- **User has `npm.ps1` shim as a script block wrapper**: resolution must prefer `npm.cmd`/`npm.exe` over `.ps1` to avoid PowerShell execution-policy prompts.
- **Path contains spaces or non-ASCII characters** (common under `C:\Users\<name with space>\`): subprocess invocations must quote correctly and Node IPC must tolerate non-ASCII `tempfile.gettempdir()` values.
- **`use_tcp=False` explicitly requested on Windows**: must raise an error with a remediation hint, not silently override the user's choice.
- **Concurrent pytest workers (`pytest-xdist`) on the same Windows host**: each worker MUST acquire a distinct ephemeral port without collisions; CI MUST run the Windows job under `-n auto` at least once per matrix cell to prove this.
- **Existing `py_pglite` entry point is still registered in a user's stale virtualenv** after upgrade: pytest must not fail-hard on duplicate plugin names; the old wheel should be uninstallable or clearly superseded.
- **Symlink or junction in `tempfile.gettempdir()` path** on Windows: socket/IPC paths must resolve without `OSError: [WinError 1920]`.
- **Python 3.14 on Windows**: the matrix includes it; any subprocess or signal API changes that land in 3.14 must not break the termination helper.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The distribution MUST be published under the name `pglite-pydb` and the importable package MUST be `pglite_pydb`; no code path may continue to expose `py_pglite` as the canonical name. No backward-compatibility shim, alias package, or runtime `DeprecationWarning` layer for the old `py_pglite` name MAY be shipped — `import py_pglite` MUST fail with a standard `ModuleNotFoundError` after upgrade.
- **FR-002**: The refactor MUST preserve existing public API shapes — class names, fixture names, configuration field names, and exception types — so that a user upgrading only rewrites `py_pglite` → `pglite_pydb` in imports.
- **FR-003**: On Linux and macOS, the default transport MUST remain a Unix domain socket; the observable default behaviour for existing users MUST NOT change.
- **FR-004**: On Windows, the default transport MUST be TCP on an automatically selected free ephemeral port unless the user explicitly supplies a port.
- **FR-005**: On Windows, if a user explicitly configures Unix-socket transport, the system MUST raise an error that identifies the platform limitation and suggests the TCP default.
- **FR-006**: The system MUST locate Node.js and npm binaries via a resolver that, on Windows, additionally searches for `.cmd` and `.exe` suffixes; when no binary is found the error MUST list every name that was searched.
- **FR-007**: The system MUST terminate the PGlite child process and every descendant it spawned on both platforms — via POSIX process groups on Linux/macOS and via a process-tree walk on Windows — within a bounded timeout, escalating from graceful to forceful termination.
- **FR-008**: The system MUST NOT leave orphaned Node processes or held TCP ports after a pytest session completes, across 100 consecutive fixture lifecycles.
- **FR-009**: The pytest plugin entry point MUST be registered as `pglite_pydb` so that `pytest --trace-config` discovers fixtures automatically after `pip install`.
- **FR-010**: All platform-specific branching MUST be gated on a single centralised `sys.platform` utility rather than scattered inline checks, so that the supported-platform matrix can be audited in one place.
- **FR-011**: CI MUST execute the test suite on both `ubuntu-latest` and `windows-latest` across every supported Python version; failures on either platform MUST block merges. At least one cell of the matrix per OS MUST run under `pytest -n auto` (xdist) so parallel-worker regressions are detected at PR time. The Node matrix MUST run Node 22 across every Python cell plus at least one Node 20 smoke cell per OS, so regressions against either supported LTS line are caught at PR time.
- **FR-011a**: The fixture MUST support `pytest-xdist` parallelism as a first-class feature on every supported OS: each xdist worker MUST obtain its own isolated PGlite instance on a distinct, non-colliding ephemeral TCP port (Windows) or socket path (Linux/macOS), without any user configuration.
- **FR-012**: Tests that exercise Unix-socket-only code paths MUST be marked with a platform skip and a human-readable reason, so that they show as skipped (not failed, not passed) on Windows.
- **FR-013**: Documentation (README, CONTRIBUTING, examples) MUST reference the new name end-to-end and MUST contain a single deprecation note directing `py-pglite` users to `pglite-pydb`.
- **FR-014**: Contributor workflows (setup, test, lint, format, clean, status) MUST be runnable on Windows PowerShell without requiring `make`, `rm`, `find`, or other Unix-shell utilities. The canonical implementation is a Python task runner (single source of truth for task logic); the existing `Makefile` is retained for Linux/macOS and MUST delegate to the same Python task logic so the two entry points cannot drift.
- **FR-015**: The refactor MUST be decomposed into 12 independently committable steps, each of which leaves the test suite green on Linux, so that any step can be bisected or reverted cleanly.
- **FR-016**: The final commit MUST raise the package version to at least `0.6.0` to signal a renamed distribution with new platform support.
- **FR-017**: The SQL-injection fix already applied to Django (commit `31bc3a2`) and any other security patches present on the pre-refactor branch MUST be preserved; the refactor must not reintroduce patched vulnerabilities.

### Key Entities *(include if feature involves data)*

- **Distribution package (`pglite-pydb`)**: the wheel/sdist artefact published to package indices; identified by name `pglite-pydb`, exposes the importable package `pglite_pydb`.
- **Importable package (`pglite_pydb`)**: the Python package directory under `src/`; owns the public API (`PGliteManager`, `PGliteConfig`, fixtures, Django/SQLAlchemy integrations).
- **Platform profile**: a small record of boolean facts about the host OS (`IS_WINDOWS`, `IS_LINUX`, `IS_MACOS`, `SUPPORTS_UNIX_SOCKETS`) used to gate transport defaults and process-management behaviour.
- **Transport configuration**: a resolved decision comprising `{mode: unix|tcp, socket_path?, host?, port?}` that the manager emits after applying platform-aware defaults to user-supplied `PGliteConfig`.
- **Node binary handle**: the absolute path discovered for `node`/`npm`, including the matched suffix (none on POSIX; `.cmd` or `.exe` on Windows).
- **Managed process tree**: the PGlite Node subprocess plus all of its descendants; on Linux identified by its process group, on Windows identified by the parent PID and walked via the OS process table.
- **Refactor step**: one of 12 atomic units of work; each has a scope, a verification command, and a commit boundary.
- **Deprecation pointer**: a single documented note mapping the legacy name `py-pglite` to the new name `pglite-pydb`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a clean Windows 11 host with Python 3.12 and either Node 20 LTS or Node 22 LTS, a new user can install `pglite-pydb[all]` and run a one-line smoke test (`SELECT 1` through the fixture) successfully in under 5 minutes, without editing any configuration file.
- **SC-002**: On Linux, the pre-refactor baseline of passing tests is equalled exactly after the refactor — zero regressions, zero newly-skipped tests on Linux.
- **SC-003**: 100 consecutive fixture setup/teardown cycles on Windows leave zero orphaned Node processes and zero TCP ports in `TIME_WAIT` attributable to the fixture.
- **SC-003a**: A pytest run invoked with `-n 4` on Windows completes with all fixtures spawning on distinct ports and zero port-collision errors across 10 consecutive invocations.
- **SC-004**: CI wall-clock time on `windows-latest` completes within 2× the wall-clock time of the equivalent `ubuntu-latest` job across the full Python matrix.
- **SC-005**: A repository-wide whole-word search for `py_pglite` or `py-pglite` returns at most one match (the intentional deprecation note in `README.md`).
- **SC-006**: The 12-step refactor is delivered as 12 commits (or 12 PRs) on the feature branch, where every single commit leaves the Linux test suite green when checked out in isolation.
- **SC-007**: A Linux user who was previously importing `py_pglite` can migrate to `pglite_pydb` by changing only import lines — zero other source edits required — and their test suite passes.
- **SC-008**: The published wheel, when installed, registers the pytest plugin name `pglite_pydb` such that `pytest --trace-config` lists it automatically; and the wheel's distribution name (as shown by `pip show`) is `pglite-pydb`.
- **SC-009**: On Windows, when Node.js is absent from `PATH`, the fixture fails within 2 seconds with an error message that names every binary variant it searched (`node`, `node.exe`, `npm`, `npm.cmd`).
- **SC-010**: Documentation review confirms that every installation example, import example, and CI badge in `README.md` and `CONTRIBUTING.md` references the new name; zero stale references remain after the final commit.

## Assumptions

- The user base is primarily Python developers using pytest for database-backed integration tests; the primary goal of the port is to unblock Windows-only contributors and CI consumers.
- Supported Node.js runtimes are Node 20 LTS and Node 22 LTS on all platforms (Linux, macOS, Windows); Node 24 Current is not supported. npm ships with both Node LTS lines. The port does not bundle Node.js — users install it themselves.
- Supported Python versions remain 3.10 through 3.14, matching the existing matrix; no change to minimum Python version is intended.
- `psutil` is already a declared dependency (confirmed in `pyproject.toml`), so the Windows process-tree termination path incurs no new third-party dependency.
- The user accepts TCP loopback (`127.0.0.1`) as the Windows transport; no support for TCP over non-loopback interfaces is required in this scope.
- Unix-domain sockets on Windows 10+ exist but are out of scope — PGlite's Node server writes socket paths that the Windows psycopg driver cannot address portably, so TCP is the only viable Windows transport.
- The refactor is a rename and cross-platform enablement, not a feature redesign: no new public API, no new storage backends, no protocol changes.
- The existing Django and SQLAlchemy integrations have test coverage sufficient to detect regressions introduced by the rename; no new test suites need to be written purely to support the port.
- A single deprecation pointer in `README.md` is the only communication for users of the old `py-pglite` / `py_pglite` name. No PyPI metadata redirect, stub package, runtime import-alias, or `DeprecationWarning` shim is in scope; `import py_pglite` must fail with `ModuleNotFoundError` after upgrade (standard Python rename semantics). The legacy `py-pglite` 0.5.3 release on PyPI is intentionally left untouched — no final release, no stub, no yanks — so existing lockfiles pinned to `py-pglite==0.5.3` continue to resolve.
- CI on `windows-latest` GitHub runners has Node.js available (or can install it via `actions/setup-node@v4`) and supports the full Python matrix used on Linux.
- Version `0.6.0` is acceptable as the release marker for the renamed distribution; semantic-versioning purists may argue for `1.0.0`, but that is a separate decision.
