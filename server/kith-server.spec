# PyInstaller build for the Kith server.
#
# Kith is meant to be a desktop app a friend can run without installing anything, and until
# now that was false in the most basic way: the .app contained an Electron shell and nothing
# else — no Python, no server, no interface — and the shell never started a backend at all.
# Its own comment said "it runs as its own process (Docker today)", and Docker is gone.
#
# So the server is frozen into one binary that the shell spawns. Three things about this
# build are load-bearing and none of them are obvious:
#
# **The interface ships inside the binary.** The Flask process serves the SPA when
# KITH_UI_DIST is set, and putting the built bundle in here means one artifact to copy and
# one origin at runtime — which is also what makes the API token injection work, since the
# page and the API are then the same origin by construction.
#
# **The persona is data, not code.** It is markdown, read at runtime, and the app starts
# with no personality at all if it is missing — with only a "0 fragments" line in the log to
# say so.
#
# **Half the dependency graph is invisible to static analysis.** APIFlask builds its schema
# layer through apispec plugins resolved by name, SQLAlchemy loads its dialect by string,
# and zoneinfo reads a data package. PyInstaller cannot see any of that, so it is listed.
#
# Build:  .venv/bin/pyinstaller kith-server.spec --noconfirm
# Output: dist/kith-server/  (a directory bundle — see below for why not --onefile)

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)
UI_DIST = HERE.parent / "ui" / "dist"

if not UI_DIST.is_dir():
    raise SystemExit(
        f"{UI_DIST} is missing — run `npm run build` in ui/ first. Shipping a server with no "
        "interface would produce a binary that starts and serves nothing."
    )

datas = [
    # Read at runtime from SERVER_ROOT, which is the bundle directory when frozen.
    (str(HERE / "persona"), "persona"),
    # The interface. Served by this process, so KITH_UI_DIST points here at startup.
    (str(UI_DIST), "ui"),
]
datas += collect_data_files("apiflask")
datas += collect_data_files("apispec")
datas += collect_data_files("tzdata")

hiddenimports = [
    # Resolved by name at runtime, so nothing imports them where PyInstaller can see it.
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    "zoneinfo",
    "encodings.idna",  # requests/urllib need it the first time a hostname is IDNA-encoded
]
# The tool registry imports its modules by walking the package, which static analysis also
# cannot follow — a missing tool here means Kith silently loses a capability.
# waitress resolves its adjustments and its logging by name, so a frozen build that only imports
# `serve` loses pieces of it at runtime.
hiddenimports += collect_submodules("waitress")
hiddenimports += collect_submodules("kith.tools")
hiddenimports += collect_submodules("apiflask")
hiddenimports += collect_submodules("apispec")

analysis = Analysis(
    ["app.py"],
    pathex=[str(HERE)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Dropped on purpose: the server never draws anything, and these pull in tens of
    # megabytes of GUI toolkit that would ship in every copy for nothing.
    excludes=["tkinter", "matplotlib", "PIL", "pytest", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="kith-server",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

# A directory bundle rather than --onefile. Onefile unpacks the whole thing to a temp
# directory on every launch, which costs seconds of startup for a process the user is
# actively waiting on, and leaves the extraction behind if the app is killed. A directory is
# also what electron-builder wants to drop into Resources.
COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="kith-server",
)
