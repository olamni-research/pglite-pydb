"""pglite-pydb command-line interface.

Exposes the `main` entry point bound by `[project.scripts] pglite-pydb` in
`pyproject.toml`. Subcommands (`backup`, `restore`, `config`) are implemented
in this package; see `cli.main` for the argparse dispatcher.
"""

from pglite_pydb.cli.main import main


__all__ = ["main"]
