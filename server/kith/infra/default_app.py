"""Which application this machine would use to open a given kind of file.

Only so a button can say "Open in Excel" instead of "Open in another app". The
opening itself never depends on this — the OS picks the handler either way — so every
failure path here returns ``None`` and the caller falls back to the generic label.

Resolved from the *extension*, not from a file, so the name is known before anything
has been copied out of the sandbox. That matters: the alternative was exporting a file
just to find out what would open it, which is a copy nobody asked for.

macOS only for now, through LaunchServices via ctypes. No dependency — pyobjc would be
a large one for a label — and no shelling out to a tool that may not be installed
(``duti`` isn't there by default). Elsewhere this returns ``None`` and the generic
label is used, which is honest rather than a guess from a table of app names.
"""

from __future__ import annotations

import ctypes
import platform
from functools import lru_cache

_UTF8 = 0x08000100
#: kLSRolesAll. Viewer-or-editor, whichever the machine prefers — we only want to
#: report what a double-click would do.
_ROLES_ALL = 0xFFFFFFFF


@lru_cache(maxsize=1)
def _launch_services():
    """The two frameworks and the symbols needed, or None if this isn't macOS.

    Cached because loading a framework per file view would be wasteful, and because
    a machine's handlers change rarely enough that a per-process answer is fine.
    """
    if platform.system() != "Darwin":
        return None
    try:
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cs = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreServices.framework/CoreServices")
    except OSError:
        return None

    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFURLCopyLastPathComponent.restype = ctypes.c_void_p
    cf.CFURLCopyLastPathComponent.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cs.UTTypeCreatePreferredIdentifierForTag.restype = ctypes.c_void_p
    cs.UTTypeCreatePreferredIdentifierForTag.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    cs.LSCopyDefaultApplicationURLForContentType.restype = ctypes.c_void_p
    cs.LSCopyDefaultApplicationURLForContentType.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    try:
        extension_tag = ctypes.c_void_p.in_dll(cs, "kUTTagClassFilenameExtension")
    except ValueError:
        return None
    return cf, cs, extension_tag


@lru_cache(maxsize=256)
def for_extension(extension: str) -> str | None:
    """The application name for an extension — "Microsoft Excel", "Preview" — or None.

    The ``.app`` suffix is dropped because the label reads better without it, and the
    name is what someone recognises: a button saying "Open in Microsoft Excel.app"
    looks like a path, not an application.
    """
    loaded = _launch_services()
    if not loaded:
        return None
    cf, cs, extension_tag = loaded

    clean = extension.lstrip(".").strip().lower()
    if not clean or not clean.isalnum():
        return None

    uti = None
    app_url = None
    try:
        tag = cf.CFStringCreateWithCString(None, clean.encode(), _UTF8)
        if not tag:
            return None
        uti = cs.UTTypeCreatePreferredIdentifierForTag(extension_tag, tag, None)
        cf.CFRelease(tag)
        if not uti:
            return None
        app_url = cs.LSCopyDefaultApplicationURLForContentType(uti, _ROLES_ALL, None)
        if not app_url:
            return None
        name = _to_text(cf, cf.CFURLCopyLastPathComponent(app_url))
        return name.removesuffix(".app") if name else None
    except Exception:
        return None
    finally:
        for ref in (uti, app_url):
            if ref:
                cf.CFRelease(ref)


def for_filename(name: str) -> str | None:
    """Same, from a whole filename. Returns None when there's no extension to go on."""
    _, _, extension = name.rpartition(".")
    return for_extension(extension) if extension and extension != name else None


def _to_text(cf, ref) -> str | None:
    if not ref:
        return None
    buffer = ctypes.create_string_buffer(1024)
    try:
        if cf.CFStringGetCString(ref, buffer, 1024, _UTF8):
            return buffer.value.decode(errors="replace")
        return None
    finally:
        cf.CFRelease(ref)
