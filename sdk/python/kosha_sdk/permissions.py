# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Permissions an add-in can ask for, with the plain words the admin sees before approving."""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    "items:read": "read items and their properties",
    "items:write": "create and change items",
    "boms:read": "read bills of materials",
    "boms:write": "create and change bills of materials",
    "files:read": "read files and drawings",
    "files:write": "add new file versions",
    "numbers:allocate": "allocate new part numbers",
    "changes:read": "read change orders and deviations",
    "erp:sync": "send released items and BOMs to an ERP and read stock and cost back",
    "notify:send": "send notifications to users",
    "ai:use": "use the AI provider configured by the admin",
    "users:read": "read user names and roles",
}


def describe_permission(name: str) -> str:
    """Plain-words label of a permission; unknown permissions are shown as they are, flagged."""
    return PERMISSIONS.get(name, f"{name} (not a standard permission)")
