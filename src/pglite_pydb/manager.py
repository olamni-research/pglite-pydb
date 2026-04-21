"""Core PGlite process management."""

import json
import logging
import os
import shutil
import subprocess  # nosec B404 - subprocess needed for npm/node process management
import sys
import time

from pathlib import Path
from textwrap import dedent
from typing import Any

import psutil

from pglite_pydb import __version__
from pglite_pydb._datadir import SIDECAR_DIRNAME
from pglite_pydb._lock import InstanceLock
from pglite_pydb._platform import IS_WINDOWS
from pglite_pydb.config import PGliteConfig
from pglite_pydb.config import SidecarConfig
from pglite_pydb.extensions import SUPPORTED_EXTENSIONS
from pglite_pydb.utils import find_pglite_modules


def _resolve_node_bin(name: str) -> str:
    """Return the absolute path of a Node binary (``node`` or ``npm``).

    On POSIX, ``shutil.which`` with the plain name is sufficient. On
    Windows, ``npm`` ships as ``npm.cmd`` and ``node`` as ``node.exe``;
    both are searched explicitly so invocation works even when PATHEXT
    has been narrowed.

    Raises:
        FileNotFoundError: lists every candidate attempted (FR-006).
    """
    candidates = [name] if not IS_WINDOWS else [name, f"{name}.cmd", f"{name}.exe"]
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise FileNotFoundError(
        f"Could not locate '{name}' on PATH. Searched: {candidates}. "
        "Install Node.js 20 LTS or 22 LTS from https://nodejs.org/"
    )


class PGliteManager:
    """Manages PGlite process lifecycle for testing.

    Framework-agnostic PGlite process manager. Provides database connections
    through framework-specific methods that require their respective dependencies.
    """

    def __init__(self, config: PGliteConfig | None = None):
        """Initialize PGlite manager.

        Args:
            config: Configuration for PGlite. If None, uses defaults.
        """
        if config is None:
            raise TypeError(
                "PGliteManager requires a PGliteConfig with an explicit data_dir "
                "(feature 003 made data_dir mandatory — see FR-001)."
            )
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        # work_dir hosts the Node runtime (package.json, pglite_manager.js,
        # node_modules). It lives under <data-dir>/.pglite-pydb/runtime/ so
        # the wrapper's infrastructure never collides with PGlite's own
        # PG_VERSION / base / global / ... data layout at the data-dir root.
        self.work_dir: Path | None = None
        self._original_cwd: str | None = None
        self._shared_engine: Any | None = None
        self._instance_lock: InstanceLock | None = None
        # Resolved TCP port for this manager instance. Populated in start()
        # when use_tcp is True. When config.tcp_port is 0 (OS-assigned), this
        # is filled in by binding a socket in-process to pick a free port;
        # otherwise equals config.tcp_port.
        self.resolved_port: int | None = None

        # Set up logging
        self.logger = logging.getLogger(__name__)
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(self.config.log_level_int)
        if not self.logger.handlers:
            self.logger.addHandler(handler)
        self.logger.setLevel(self.config.log_level_int)

    def __enter__(self) -> "PGliteManager":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.stop()

    def _prepare_data_dir(self) -> Path:
        """Prepare data directory + Node-runtime work dir.

        The wrapper's Node runtime (package.json, pglite_manager.js,
        node_modules) lives under ``<data-dir>/.pglite-pydb/runtime/`` so
        PGlite's own data layout at ``<data-dir>/`` is never polluted.
        On first start, the sidecar config is created (empty
        ``backup_location``) if absent.
        """
        assert self.config.data_dir is not None  # enforced in PGliteConfig.__post_init__
        data_dir = Path(self.config.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        sidecar = data_dir / SIDECAR_DIRNAME
        sidecar.mkdir(parents=True, exist_ok=True)
        # First-start sidecar config: only write if absent (preserve any
        # operator-configured backup_location on subsequent starts).
        cfg_path = sidecar / "config.json"
        if not cfg_path.exists():
            SidecarConfig().save(data_dir)
        # Override of Node runtime location via config.work_dir (rare, but
        # tests may want to share node_modules across many instances).
        if self.config.work_dir:
            work_dir = self.config.work_dir
            work_dir.mkdir(parents=True, exist_ok=True)
        else:
            work_dir = sidecar / "runtime"
            work_dir.mkdir(parents=True, exist_ok=True)

        # Create package.json if it doesn't exist
        package_json = work_dir / "package.json"
        if not package_json.exists():
            package_content = {
                "name": "pglite-pydb-env",
                "version": __version__,
                "description": "PGlite test environment for pglite-pydb",
                "scripts": {"start": "node pglite_manager.js"},
                "dependencies": {
                    "@electric-sql/pglite": "^0.3.0",
                    "@electric-sql/pglite-socket": "^0.0.8",
                },
            }
            with open(package_json, "w") as f:
                json.dump(package_content, f, indent=2)

        # Create pglite_manager.js if it doesn't exist
        manager_js = work_dir / "pglite_manager.js"
        if not manager_js.exists():
            # Generate JavaScript for extensions
            ext_requires = []
            ext_configs = []
            if self.config.extensions:
                for ext_name in self.config.extensions:
                    ext_info = SUPPORTED_EXTENSIONS[ext_name]
                    ext_requires.append(
                        f"const {{ {ext_info['name']} }} = require('{ext_info['module']}');"
                    )
                    ext_configs.append(f"    {ext_name}: {ext_info['name']}")

            ext_requires_str = "\n".join(ext_requires)
            ext_configs_str = ",\n".join(ext_configs)
            extensions_obj_str = f"{{\n{ext_configs_str}\n}}" if ext_configs else "{}"

            # Generate JavaScript content based on socket mode. Embed the
            # resolved data_dir so PGlite persists to it (FR-001..FR-004).
            # json.dumps gives correct JS string escaping on Windows paths.
            data_dir_literal = json.dumps(str(self.config.data_dir))
            if self.config.use_tcp:
                js_content = self._generate_tcp_js_content(
                    ext_requires_str, extensions_obj_str, data_dir_literal
                )
            else:
                js_content = self._generate_unix_js_content(
                    ext_requires_str, extensions_obj_str, data_dir_literal
                )
            with open(manager_js, "w") as f:
                f.write(js_content)

        return work_dir

    def _generate_unix_js_content(
        self,
        ext_requires_str: str,
        extensions_obj_str: str,
        data_dir_literal: str,
    ) -> str:
        """Generate JavaScript content for Unix socket mode (original logic)."""
        return dedent(f"""
            const {{ PGlite }} = require('@electric-sql/pglite');
            const {{ PGLiteSocketServer }} = require('@electric-sql/pglite-socket');
            const fs = require('fs');
            const path = require('path');
            const {{ unlink }} = require('fs/promises');
            const {{ existsSync }} = require('fs');
            {ext_requires_str}

            const SOCKET_PATH = '{self.config.socket_path}';
            const DATA_DIR = {data_dir_literal};

            async function cleanup() {{
                if (existsSync(SOCKET_PATH)) {{
                    try {{
                        await unlink(SOCKET_PATH);
                        console.log(`Removed old socket at ${{SOCKET_PATH}}`);
                    }} catch (err) {{
                        // Ignore errors during cleanup
                    }}
                }}
            }}

            async function startServer() {{
                try {{
                    // Create a PGlite instance bound to the mandatory data_dir
                    const db = new PGlite({{
                        dataDir: DATA_DIR,
                        extensions: {extensions_obj_str}
                    }});

                    // Clean up any existing socket
                    await cleanup();

                    // Create and start a socket server
                    const server = new PGLiteSocketServer({{
                        db,
                        path: SOCKET_PATH,
                    }});
                    await server.start();
                    console.log(`Server started on socket ${{SOCKET_PATH}}`);

                    // Handle graceful shutdown
                    process.on('SIGINT', async () => {{
                        console.log('Received SIGINT, shutting down gracefully...');
                        try {{
                            await server.stop();
                            await db.close();
                            console.log('Server stopped and database closed');
                        }} catch (err) {{
                            console.error('Error during shutdown:', err);
                        }}
                        process.exit(0);
                    }});

                    process.on('SIGTERM', async () => {{
                        console.log('Received SIGTERM, shutting down gracefully...');
                        try {{
                            await server.stop();
                            await db.close();
                            console.log('Server stopped and database closed');
                        }} catch (err) {{
                            console.error('Error during shutdown:', err);
                        }}
                        process.exit(0);
                    }});

                    // Keep the process alive
                    process.on('exit', () => {{
                        console.log('Process exiting...');
                    }});

                }} catch (err) {{
                    console.error('Failed to start PGlite server:', err);
                    process.exit(1);
                }}
            }}

            startServer();
        """).strip()

    def _generate_tcp_js_content(
        self,
        ext_requires_str: str,
        extensions_obj_str: str,
        data_dir_literal: str,
    ) -> str:
        """Generate JavaScript content for TCP socket mode."""
        # resolved_port is set by start() before this is called; fall back to
        # config.tcp_port in the (theoretical) case of direct invocation.
        port = self.resolved_port or self.config.tcp_port
        return dedent(f"""
            const {{ PGlite }} = require('@electric-sql/pglite');
            const {{ PGLiteSocketServer }} = require('@electric-sql/pglite-socket');
            const fs = require('fs');
            const path = require('path');
            {ext_requires_str}

            const DATA_DIR = {data_dir_literal};

            async function startServer() {{
                try {{
                    // Create a PGlite instance bound to the mandatory data_dir
                    const db = new PGlite({{
                        dataDir: DATA_DIR,
                        extensions: {extensions_obj_str}
                    }});

                    // Create and start a TCP server
                    const server = new PGLiteSocketServer({{
                        db,
                        host: '{self.config.tcp_host}',
                        port: {port}
                    }});
                    await server.start();
                    console.log(`Server started on TCP {self.config.tcp_host}:{port}`);

                    // Handle graceful shutdown
                    process.on('SIGINT', async () => {{
                        console.log('Received SIGINT, shutting down gracefully...');
                        try {{
                            await server.stop();
                            await db.close();
                            console.log('Server stopped and database closed');
                        }} catch (err) {{
                            console.error('Error during shutdown:', err);
                        }}
                        process.exit(0);
                    }});

                    process.on('SIGTERM', async () => {{
                        console.log('Received SIGTERM, shutting down gracefully...');
                        try {{
                            await server.stop();
                            await db.close();
                            console.log('Server stopped and database closed');
                        }} catch (err) {{
                            console.error('Error during shutdown:', err);
                        }}
                        process.exit(0);
                    }});

                    // Keep the process alive
                    process.on('exit', () => {{
                        console.log('Process exiting...');
                    }});

                }} catch (err) {{
                    console.error('Failed to start PGlite server:', err);
                    process.exit(1);
                }}
            }}

            startServer();
        """).strip()

    def _cleanup_socket(self) -> None:
        """Clean up the PGlite socket file."""
        # Skip cleanup for TCP mode
        if self.config.use_tcp:
            return

        socket_path = Path(self.config.socket_path)
        if socket_path.exists():
            try:
                socket_path.unlink()
                self.logger.info(f"Cleaned up socket at {socket_path}")
            except Exception as e:
                self.logger.warning(f"Failed to clean up socket: {e}")

    def _kill_existing_processes(self) -> None:
        """Kill any existing PGlite processes that might conflict with this socket."""
        try:
            # Fix for issue #31: Compare work directory, not socket directory
            # Socket and work directories are different by design for isolation
            # Use work_dir if available, otherwise fall back to socket directory
            if hasattr(self, "work_dir") and self.work_dir:
                my_target_dir = str(self.work_dir)
                comparison_type = "work directory"
            else:
                my_target_dir = str(Path(self.config.socket_path).parent)
                comparison_type = "socket directory"

            for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
                if proc.info["cmdline"] and any(
                    "pglite_manager.js" in cmd for cmd in proc.info["cmdline"]
                ):
                    # Use exact directory match to avoid killing processes in similar paths
                    try:
                        proc_cwd = proc.info.get("cwd", "")
                        if proc_cwd == my_target_dir:
                            pid = proc.info["pid"]
                            self.logger.info(
                                f"Killing existing PGlite process: {pid} (matching {comparison_type})"
                            )
                            proc.kill()
                            proc.wait(timeout=5)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        # Process already gone or can't access it
                        continue
        except Exception as e:
            self.logger.warning(f"Error killing existing PGlite processes: {e}")

    def _kill_all_pglite_processes(self) -> None:
        """Kill all PGlite processes globally (more aggressive cleanup for termination)."""
        try:
            killed_processes = []
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                if proc.info["cmdline"] and any(
                    "pglite_manager.js" in cmd for cmd in proc.info["cmdline"]
                ):
                    try:
                        pid = proc.info["pid"]
                        self.logger.info(f"Killing PGlite process globally: {pid}")
                        proc.kill()
                        proc.wait(timeout=5)
                        killed_processes.append(pid)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        # Process already gone or can't access it
                        continue

            if killed_processes:
                self.logger.info(
                    f"Killed {len(killed_processes)} PGlite processes: {killed_processes}"
                )
        except Exception as e:
            self.logger.warning(f"Error killing all PGlite processes: {e}")

    def _install_dependencies(self, work_dir: Path) -> None:
        """Install npm dependencies if needed."""
        if not self.config.auto_install_deps:
            return

        node_modules = work_dir / "node_modules"
        if self.config.node_modules_check and not node_modules.exists():
            self.logger.info("Installing npm dependencies...")
            # nosec B603,B607 - npm install with fixed args, safe for testing library
            result = subprocess.run(
                [_resolve_node_bin("npm"), "install"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,  # Add timeout for npm install
            )
            self.logger.info(f"npm install completed: {result.stdout}")

    def _resolve_tcp_port(self) -> int:
        """Resolve the TCP port for this manager's lifetime.

        When ``config.tcp_port == 0`` the OS is asked for a free ephemeral
        port via an in-process bind; the selected port is returned so the
        Node subprocess can bind to it and so the readiness probe targets
        the same port. When ``config.tcp_port`` is a concrete value it is
        returned unchanged. See research.md R4.
        """
        if self.config.tcp_port == 0:
            import socket as _socket

            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.bind((self.config.tcp_host, 0))
                return int(s.getsockname()[1])
        return int(self.config.tcp_port)

    def start(self) -> None:
        """Start the PGlite server."""
        if self.process is not None:
            self.logger.warning("PGlite process already running")
            return

        # Resolve TCP port (a no-op for Unix-socket mode, a real pick for
        # config.tcp_port==0 e.g. the Windows auto-promoted default).
        if self.config.use_tcp:
            self.resolved_port = self._resolve_tcp_port()

        # Refuse to start on a data-dir flagged by a mid-restore failure
        # (T062 sentinel written by BackupEngine.restore_full_snapshot).
        assert self.config.data_dir is not None
        from pglite_pydb.errors import InvalidDataDirError

        sentinel = Path(self.config.data_dir) / SIDECAR_DIRNAME / "FAILED_RESTORE"
        if sentinel.exists():
            raise InvalidDataDirError(
                self.config.data_dir,
                f"FAILED_RESTORE sentinel present at {sentinel}. A previous "
                "restore did not complete. Inspect the directory, repair or "
                "re-run restore, then delete the sentinel file to proceed.",
            )

        # Acquire the cross-platform advisory instance lock BEFORE any
        # filesystem mutation under data_dir (FR-006, FR-017, FR-033).
        self._instance_lock = InstanceLock(self.config.data_dir).acquire()

        try:
            # Setup work directory first so it's available for cleanup logic
            self.work_dir = self._prepare_data_dir()

            # Setup
            self._kill_existing_processes()
            self._cleanup_socket()

            self._original_cwd = os.getcwd()
            os.chdir(self.work_dir)
        except BaseException:
            if self._instance_lock is not None:
                self._instance_lock.release()
                self._instance_lock = None
            raise

        try:
            # Install dependencies
            self._install_dependencies(self.work_dir)

            # Prepare environment for Node.js process
            env = os.environ.copy()
            if self.config.node_options:
                env["NODE_OPTIONS"] = self.config.node_options
                self.logger.info(
                    f"Using custom NODE_OPTIONS: {self.config.node_options}"
                )

            # Ensure Node.js can find the required modules
            node_modules_path = find_pglite_modules(self.work_dir)
            if node_modules_path:
                env["NODE_PATH"] = str(node_modules_path)
                self.logger.info(f"Setting NODE_PATH to: {node_modules_path}")

            # Start PGlite process with limited output buffering
            self.logger.info("Starting PGlite server...")
            # nosec B603,B607 - node with fixed script, safe for testing library
            self.process = subprocess.Popen(
                [_resolve_node_bin("node"), "pglite_manager.js"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Combine stderr with stdout
                text=True,
                bufsize=0,  # Unbuffered for real-time monitoring
                universal_newlines=True,
                env=env,
                # POSIX only: create a new process group so we can killpg on shutdown.
                # Windows has no process-group signal semantics; _terminate_process_tree
                # handles teardown there via psutil descendant walking.
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            )

            # Wait for startup with robust monitoring
            start_time = time.time()
            ready_logged = False

            while time.time() - start_time < self.config.timeout:
                # Check if process died
                if self.process.poll() is not None:
                    # Get output with timeout to prevent hanging
                    try:
                        stdout, _ = self.process.communicate(timeout=2)
                        output = (
                            stdout[:1000] if stdout else "No output"
                        )  # Limit output
                    except subprocess.TimeoutExpired:
                        output = "Process output timeout"

                    raise RuntimeError(
                        f"PGlite process died during startup. Output: {output}"
                    )

                # Check readiness based on socket mode
                if self.config.use_tcp:
                    # TCP readiness check — always probe the resolved port
                    # (which equals config.tcp_port when the user set it
                    # explicitly; otherwise filled in by _resolve_tcp_port).
                    probe_port = self.resolved_port or self.config.tcp_port
                    if not ready_logged:
                        self.logger.info(
                            f"Waiting for TCP server on {self.config.tcp_host}:{probe_port}..."
                        )
                        ready_logged = True

                    try:
                        import socket

                        test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        test_socket.settimeout(1)
                        test_socket.connect((self.config.tcp_host, probe_port))
                        test_socket.close()
                        self.logger.info(
                            f"PGlite TCP server started successfully on {self.config.tcp_host}:{probe_port}"
                        )
                        break
                    except (ImportError, OSError):
                        # TCP port not ready yet, continue waiting
                        pass
                else:
                    # Unix socket readiness check
                    socket_path = Path(self.config.socket_path)
                    if socket_path.exists() and not ready_logged:
                        self.logger.info(
                            "PGlite socket created, server should be ready..."
                        )
                        ready_logged = True

                        # Test basic connectivity to ensure it's really ready
                        try:
                            import socket

                            test_socket = socket.socket(
                                socket.AF_UNIX, socket.SOCK_STREAM
                            )
                            test_socket.settimeout(1)
                            test_socket.connect(str(socket_path))
                            test_socket.close()
                            self.logger.info("PGlite server started successfully")
                            break
                        except (ImportError, OSError):
                            # Socket exists but not ready yet, continue waiting
                            pass

                time.sleep(0.5)  # Check more frequently for better responsiveness
            else:
                # Timeout - cleanup and raise error
                if self.process and self.process.poll() is None:
                    self.logger.warning("PGlite server startup timeout, terminating...")
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.logger.warning("Force killing PGlite process...")
                        self.process.kill()
                        self.process.wait()

                raise RuntimeError(
                    f"PGlite server failed to start within {self.config.timeout} seconds"
                )

        except BaseException:
            # If startup failed after lock acquisition, release the lock so
            # the operator can retry without a stale hold.
            if self._instance_lock is not None:
                self._instance_lock.release()
                self._instance_lock = None
            raise
        finally:
            # Restore working directory
            if self._original_cwd:
                os.chdir(self._original_cwd)

    def _terminate_process_tree(
        self, proc: "subprocess.Popen[str]", timeout: float = 5.0
    ) -> None:
        """Gracefully-then-forcefully terminate a subprocess plus every descendant.

        POSIX path (Linux/macOS, preserved): signal the entire process
        group via ``os.killpg`` (SIGTERM, then SIGKILL on timeout). The
        subprocess was spawned with ``preexec_fn=os.setsid`` so it owns
        its own PGID.

        Windows path (new): walk the live process tree via ``psutil``,
        call ``.terminate()`` on every descendant plus the parent, wait
        for graceful exit, then ``.kill()`` anything still running. Windows
        has no POSIX process-group signal semantics.

        See research.md R3 / data-model.md Entity 6.
        """
        # POSIX first — existing behaviour preserved
        if hasattr(os, "killpg") and hasattr(proc, "pid"):
            try:
                os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM
                self.logger.debug("Sent SIGTERM to POSIX process group")
            except (OSError, ProcessLookupError):
                # Group may already be gone; fall back to plain terminate
                proc.terminate()

            try:
                proc.wait(timeout=timeout)
                self.logger.info("PGlite server stopped gracefully")
                return
            except subprocess.TimeoutExpired:
                self.logger.warning(
                    "PGlite process didn't stop gracefully, force killing..."
                )

            try:
                os.killpg(os.getpgid(proc.pid), 9)  # SIGKILL
                self.logger.debug("Sent SIGKILL to POSIX process group")
            except (OSError, ProcessLookupError):
                proc.kill()

            try:
                proc.wait(timeout=2)
                self.logger.info("PGlite server stopped forcefully")
            except subprocess.TimeoutExpired:
                self.logger.error("Failed to kill PGlite process group!")
                self._kill_all_pglite_processes()
            return

        # Windows path — walk the tree via psutil
        try:
            parent = psutil.Process(proc.pid)
        except psutil.NoSuchProcess:
            # Already gone; nothing to do
            return

        try:
            children = parent.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []

        # Graceful phase
        for p in (*children, parent):
            try:
                p.terminate()
            except psutil.NoSuchProcess:
                continue

        try:
            proc.wait(timeout=timeout)
            self.logger.info("PGlite server stopped gracefully (Windows tree)")
        except subprocess.TimeoutExpired:
            self.logger.warning(
                "PGlite process tree did not exit within %ss; forcing kill",
                timeout,
            )
            for p in (*children, parent):
                try:
                    if p.is_running():
                        p.kill()
                except psutil.NoSuchProcess:
                    continue
            try:
                proc.wait(timeout=2)
                self.logger.info("PGlite server tree killed (Windows)")
            except subprocess.TimeoutExpired:
                self.logger.error(
                    "PGlite process tree survived SIGKILL equivalent; "
                    "falling back to global pglite cleanup"
                )
                self._kill_all_pglite_processes()

    def stop(self) -> None:
        """Stop the PGlite server."""
        if self.process is None:
            # Still release the lock if it happens to be held (e.g. start()
            # acquired the lock then failed before spawning Node).
            if self._instance_lock is not None:
                self._instance_lock.release()
                self._instance_lock = None
            return

        try:
            self.logger.debug("Terminating PGlite process tree...")
            self._terminate_process_tree(self.process)

        except Exception as e:
            self.logger.warning(f"Error stopping PGlite: {e}")
        finally:
            self.process = None
            # Additional cleanup: kill any remaining pglite processes
            # Note: Global cleanup is only used in error conditions, not normal stop
            if self.config.cleanup_on_exit:
                self._cleanup_socket()
            # Release the FR-006 instance lock last, after the subprocess
            # is gone — otherwise a second wrapper could race in while we
            # are still tearing down.
            if self._instance_lock is not None:
                self._instance_lock.release()
                self._instance_lock = None

    def is_running(self) -> bool:
        """Check if PGlite process is running."""
        return self.process is not None and self.process.poll() is None

    def get_connection_string(self) -> str:
        """Get the database connection string for framework-agnostic usage.

        Returns:
            PostgreSQL connection string

        Raises:
            RuntimeError: If PGlite server is not running
        """
        if not self.is_running():
            raise RuntimeError("PGlite server is not running. Call start() first.")

        return self.config.get_connection_string()

    def get_dsn(self) -> str:
        """Get the database DSN string for framework-agnostic usage.

        Returns:
            PostgreSQL DSN string
        """
        if not self.is_running():
            raise RuntimeError("PGlite server is not running. Call start() first.")

        return self.config.get_dsn()

    def wait_for_ready_basic(self, max_retries: int = 15, delay: float = 1.0) -> bool:
        """Wait for database to be ready using framework-agnostic connection test.

        Args:
            max_retries: Maximum number of connection attempts
            delay: Delay between attempts in seconds

        Returns:
            True if database becomes ready, False otherwise
        """
        from pglite_pydb.utils import check_connection

        for attempt in range(max_retries):
            try:
                # Use DSN format for direct psycopg connection testing
                if check_connection(self.config.get_dsn()):
                    self.logger.info(f"Database ready after {attempt + 1} attempts")
                    time.sleep(0.2)  # Small stability delay
                    return True
            except Exception as e:
                self.logger.warning(
                    f"Database not ready (attempt {attempt + 1}/{max_retries}): {e}"
                )

            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                self.logger.error(
                    f"Database failed to become ready after {max_retries} attempts"
                )

        return False

    def wait_for_ready(self, max_retries: int = 15, delay: float = 1.0) -> bool:
        """Wait for database to be ready (framework-agnostic).

        This is an alias for wait_for_ready_basic() to maintain API consistency
        across different manager types while keeping the base manager framework-agnostic.

        Args:
            max_retries: Maximum number of connection attempts
            delay: Delay between attempts in seconds

        Returns:
            True if database becomes ready, False otherwise
        """
        return self.wait_for_ready_basic(max_retries=max_retries, delay=delay)

    def restart(self) -> None:
        """Restart the PGlite server.

        Stops the current server if running and starts a new one.
        """
        if self.is_running():
            self.stop()
        self.start()

    def get_psycopg_uri(self) -> str:
        """Get the database URI for psycopg usage.

        Returns:
            PostgreSQL URI string compatible with psycopg

        Raises:
            RuntimeError: If PGlite server is not running
        """
        if not self.is_running():
            raise RuntimeError("PGlite server is not running. Call start() first.")

        return self.config.get_psycopg_uri()

    def get_asyncpg_uri(self) -> str:
        """Get the database URI for asyncpg usage.

        Returns:
            PostgreSQL URI string compatible with asyncpg.connect()

        Raises:
            RuntimeError: If PGlite server is not running
        """
        if not self.is_running():
            raise RuntimeError("PGlite server is not running. Call start() first.")

        return self.config.get_asyncpg_uri()
