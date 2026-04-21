"""Shared interactive-confirmation helpers for the pglite-pydb CLI.

Implements the TTY × --assume-yes matrix from research §R6 and the
FR-035 second-confirmation split.
"""

from __future__ import annotations

import sys

from pglite_pydb.errors import ConfirmationDeclinedError
from pglite_pydb.errors import ConfirmationRequiredError


def _is_tty() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def _confirm(prompt: str, *, assume_yes: bool) -> bool:
    """First-level confirmation (FR-021, FR-022, FR-025).

    TTY + !assume_yes → interactive [y/N] prompt.
    TTY +  assume_yes → print auto-confirm message, return True.
    !TTY +  assume_yes → print auto-confirm message, return True.
    !TTY + !assume_yes → raise ConfirmationRequiredError.

    User reply "y"/"yes" (case-insensitive) → True.
    Anything else → raise ConfirmationDeclinedError.
    """
    if assume_yes:
        print(f"{prompt} (auto-confirmed via --assume-yes)", file=sys.stderr)
        return True
    if not _is_tty():
        raise ConfirmationRequiredError(prompt, flag="--assume-yes")
    reply = input(f"{prompt} [y/N]: ").strip().lower()
    if reply in {"y", "yes"}:
        return True
    raise ConfirmationDeclinedError(prompt)


def _confirm_destroy(prompt: str, *, assume_yes_destroy: bool) -> bool:
    """Second-level confirmation for FR-035 (non-empty full-snapshot target).

    Requires its OWN flag (``--assume-yes-destroy``) — ``--assume-yes`` alone
    does NOT satisfy this prompt. Interactive users must type the literal
    word ``DESTROY`` (case-sensitive) to proceed; anything else aborts.
    """
    if assume_yes_destroy:
        print(
            f"{prompt} (auto-confirmed via --assume-yes-destroy)", file=sys.stderr
        )
        return True
    if not _is_tty():
        raise ConfirmationRequiredError(prompt, flag="--assume-yes-destroy")
    reply = input(f"{prompt} Type DESTROY to proceed: ").strip()
    if reply == "DESTROY":
        return True
    raise ConfirmationDeclinedError(prompt)


__all__ = ["_confirm", "_confirm_destroy"]
