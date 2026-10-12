# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Kosha add-in SDK.

Everything an add-in needs to plug into Kosha: the manifest model, the context the server
hands to an add-in, base classes for each kind of add-in, an HTTP client with safe defaults,
event hooks and a conformance test kit. The SDK has no dependency on the Kosha server, so
add-ins can be built and tested on their own.
"""
from .context import AddinContext, Health
from .errors import AddinError, ManifestError
from .manifest import Manifest, load_manifest
from .permissions import PERMISSIONS, describe_permission

__version__ = "0.5.0"
SDK_API_VERSION = 1

__all__ = [
    "AddinContext", "AddinError", "Health", "Manifest", "ManifestError", "PERMISSIONS",
    "describe_permission", "load_manifest", "__version__", "SDK_API_VERSION",
]
