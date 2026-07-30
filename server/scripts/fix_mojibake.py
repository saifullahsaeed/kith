#!/usr/bin/env python3
"""One-off repair for text that was stored as mojibake.

Until the streaming clients pinned ``response.encoding = "utf-8"``, ``requests``
fell back to latin-1 on responses with no charset — so every em dash, curly
quote and accent the model wrote landed in the brain as ``â€"``-style garbage.
The clients are fixed; this walks what's already stored and un-mangles it.

    python scripts/fix_mojibake.py                    # dry run, shows the diff
    python scripts/fix_mojibake.py --apply            # rewrite the DB
    python scripts/fix_mojibake.py --apply --sandbox  # ...and his files too

The repair is the inverse of the mistake (``s.encode("latin-1").decode("utf-8")``)
and only runs on strings that round-trip cleanly, so text that was never broken
is left exactly as it is.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "agent.db"
SANDBOX_CONTAINER = "kith-sandbox"
SANDBOX_HOME = "/home/kith"
# Cheap gate: mojibake always leaves one of these lead bytes behind.
MARKERS = ("Ã", "Â", "â", "ð", "Î", "Ñ")


def repair(text: str, rounds: int = 3) -> str:
    """Undo up to `rounds` layers of latin-1/utf-8 mangling."""
    out = text
    for _ in range(rounds):
        if not any(m in out for m in MARKERS):
            break
        try:
            candidate = out.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if candidate == out or "�" in candidate:
            break
        out = candidate
    return out


def text_columns(conn: sqlite3.Connection) -> list[tuple[str, list[str]]]:
    tables = [
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not r[0].startswith("sqlite_")
    ]
    out = []
    for table in tables:
        cols = [
            r[1]
            for r in conn.execute(f'PRAGMA table_info("{table}")')
            if (r[2] or "").upper().startswith(("TEXT", "VARCHAR", "CHAR", "CLOB")) or not r[2]
        ]
        if cols:
            out.append((table, cols))
    return out


def fix_db(apply: bool) -> int:
    if not DB.exists():
        print(f"no database at {DB}", file=sys.stderr)
        return 0
    if apply:
        backup = DB.with_suffix(".db.bak")
        shutil.copy2(DB, backup)
        print(f"backup → {backup}")

    conn = sqlite3.connect(DB)
    hits = 0
    for table, cols in text_columns(conn):
        quoted = ", ".join(f'"{c}"' for c in cols)
        rows = conn.execute(f'SELECT rowid, {quoted} FROM "{table}"').fetchall()
        for row in rows:
            rowid, values = row[0], row[1:]
            changes = {}
            for col, value in zip(cols, values):
                if not isinstance(value, str):
                    continue
                fixed = repair(value)
                if fixed != value:
                    changes[col] = fixed
            if not changes:
                continue
            hits += len(changes)
            for col, fixed in changes.items():
                print(f"  {table}.{col} #{rowid}: {_preview(fixed)}")
                if apply:
                    conn.execute(f'UPDATE "{table}" SET "{col}" = ? WHERE rowid = ?', (fixed, rowid))
    if apply:
        conn.commit()
    conn.close()
    return hits


def fix_sandbox(apply: bool) -> int:
    """Repair the files in his own computer (a sibling container)."""
    script = f"""
import pathlib, sys
MARKERS = {MARKERS!r}
def repair(text, rounds=3):
    out = text
    for _ in range(rounds):
        if not any(m in out for m in MARKERS):
            break
        try:
            cand = out.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if cand == out or "\\ufffd" in cand:
            break
        out = cand
    return out

apply = {apply!r}
for p in pathlib.Path({SANDBOX_HOME!r}).rglob("*"):
    if not p.is_file() or p.stat().st_size > 5_000_000:
        continue
    try:
        raw = p.read_text("utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    fixed = repair(raw)
    if fixed != raw:
        print("  " + str(p))
        if apply:
            p.write_text(fixed, "utf-8")
"""
    proc = subprocess.run(
        ["docker", "exec", "-i", SANDBOX_CONTAINER, "python3", "-"],
        input=script.encode(),
        capture_output=True,
    )
    out = proc.stdout.decode(errors="replace").strip()
    if proc.returncode != 0:
        print(proc.stderr.decode(errors="replace").strip(), file=sys.stderr)
        return 0
    if out:
        print(out)
    return len([line for line in out.splitlines() if line.strip()])


def _preview(text: str, width: int = 90) -> str:
    one_line = " ".join(text.split())
    return one_line[:width] + ("…" if len(one_line) > width else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the repairs (default: dry run)")
    ap.add_argument("--sandbox", action="store_true", help="also repair files in his sandbox")
    args = ap.parse_args()

    print("── brain ──")
    db_hits = fix_db(args.apply)
    sb_hits = 0
    if args.sandbox:
        print("── his files ──")
        sb_hits = fix_sandbox(args.apply)

    verb = "repaired" if args.apply else "would repair"
    print(f"\n{verb} {db_hits} field(s)" + (f" and {sb_hits} file(s)" if args.sandbox else ""))
    if not args.apply and (db_hits or sb_hits):
        print("re-run with --apply to write it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
