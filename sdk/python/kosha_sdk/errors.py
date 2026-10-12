# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Errors an add-in raises on purpose. Anything else is treated as a crash by the server's guard."""
from __future__ import annotations


class AddinError(Exception):
    """An expected failure with a message a user can act on (wrong password, record not found ...).

    The server shows the message as it is, does not count it as a crash, and never disables
    the add-in because of it.
    """

    def __init__(self, message: str, *, status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


class ManifestError(ValueError):
    """The manifest is missing, unreadable or breaks a rule. The message names the field."""
