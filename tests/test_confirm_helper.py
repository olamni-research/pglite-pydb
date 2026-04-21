"""Unit tests for the shared confirmation helpers (T011).

Covers the TTY × --assume-yes matrix from research §R6 plus the FR-035
second-confirmation split.
"""

from __future__ import annotations

import sys

import pytest

from pglite_pydb.cli._confirm import _confirm
from pglite_pydb.cli._confirm import _confirm_destroy
from pglite_pydb.errors import ConfirmationDeclinedError
from pglite_pydb.errors import ConfirmationRequiredError


def _patch_stdin(monkeypatch: pytest.MonkeyPatch, *, is_tty: bool) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: is_tty, raising=False)


# --- _confirm (first-level) -----------------------------------------------


def test_tty_no_assume_yes_yes_reply_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert _confirm("proceed?", assume_yes=False) is True


def test_tty_no_assume_yes_no_reply_raises_declined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    with pytest.raises(ConfirmationDeclinedError):
        _confirm("proceed?", assume_yes=False)


def test_tty_with_assume_yes_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdin(monkeypatch, is_tty=True)

    def _boom(prompt: str = "") -> str:
        raise AssertionError("input() should not be called when --assume-yes")

    monkeypatch.setattr("builtins.input", _boom)
    assert _confirm("proceed?", assume_yes=True) is True


def test_non_tty_no_assume_yes_raises_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin(monkeypatch, is_tty=False)
    with pytest.raises(ConfirmationRequiredError) as excinfo:
        _confirm("proceed?", assume_yes=False)
    assert "--assume-yes" in str(excinfo.value)


def test_non_tty_with_assume_yes_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdin(monkeypatch, is_tty=False)
    assert _confirm("proceed?", assume_yes=True) is True


# --- _confirm_destroy (second-level, FR-035) ------------------------------


def test_destroy_tty_requires_DESTROY_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "DESTROY")
    assert _confirm_destroy("wipe?", assume_yes_destroy=False) is True


def test_destroy_tty_wrong_word_declined(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    with pytest.raises(ConfirmationDeclinedError):
        _confirm_destroy("wipe?", assume_yes_destroy=False)


def test_destroy_non_tty_without_destroy_flag_raises_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin(monkeypatch, is_tty=False)
    with pytest.raises(ConfirmationRequiredError) as excinfo:
        _confirm_destroy("wipe?", assume_yes_destroy=False)
    # FR-035 split: only --assume-yes-destroy unblocks this prompt.
    assert "--assume-yes-destroy" in str(excinfo.value)


def test_destroy_non_tty_with_destroy_flag_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin(monkeypatch, is_tty=False)
    assert _confirm_destroy("wipe?", assume_yes_destroy=True) is True
