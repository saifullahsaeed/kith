# PyInstaller build for the Kith command line.
#
# Separate from `kith-server.spec` rather than a second EXE inside it, and the reason is
# startup time. The server bundle carries Flask, APIFlask, SQLAlchemy, waitress, every tool
# module and the whole built interface, because it needs all of it. The CLI needs none of it:
# it is a client that speaks HTTP with the standard library, and the only thing it imports
# from the server is `kith.settings` — 40 lines of paths that import nothing themselves.
#
# That difference is the entire design. This binary is invoked once per message by another
# agent, so its import cost is paid on every single call, and a shared bundle would mean
# paying for a web framework to print a table.
#
# Build:  .venv/bin/pyinstaller kith-cli.spec --noconfirm
# Output: dist/kith/kith
#
# `kith install` is what puts it on PATH, by symlink. Not a copy: an app update replaces the
# binary in the bundle, and a copy in ~/.local/bin would go on running the old one silently.

from pathlib import Path

HERE = Path(SPECPATH)

analysis = Analysis(
    ["kith/cli/__main__.py"],
    pathex=[str(HERE)],
    binaries=[],
    # Nothing. The CLI reads no data files — no persona, no skills, no interface. Anything it
    # needs to know about the machine it asks the server for, which is what being a client
    # means, and a data file here would be a second copy of something the server owns.
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # The server's exclusion list, plus the whole server. `kith.settings` is pulled in by the
    # import graph; everything else under `kith.` is not, and excluding it explicitly turns a
    # stray import added later into a *build* failure rather than a forty-megabyte binary
    # nobody notices.
    excludes=[
        "tkinter",
        "matplotlib",
        "PIL",
        "pytest",
        "PyInstaller",
        "flask",
        "apiflask",
        "apispec",
        "sqlalchemy",
        "waitress",
        "requests",
        "kith.api",
        "kith.app",
        "kith.services",
        "kith.infra",
        "kith.engine",
        "kith.llm",
        "kith.tools",
        "kith.domain",
        "kith.kernel",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="kith",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

# A directory bundle, for the reason the server is one: onefile unpacks to a temporary
# directory on every launch. For the server that costs a second of startup once. For a command
# invoked per message it would be the dominant cost of the whole CLI.
COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="kith",
)
