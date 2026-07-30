"""Kith's sandbox — a Docker container that is its own little computer.

It is genuinely *his* machine: he has **root** inside it and can do whatever he
likes — install packages, run servers, rewrite his own system, make a mess. The
isolation is the wall around the *host*, not a leash on him:

- **No host filesystem mounts** — he cannot see or touch your files.
- **No Docker socket** and **not privileged** — he cannot control Docker, create
  privileged containers, or escape to your Mac.
- **Generous resource ceilings** — the one thing that *is* capped, purely so a
  runaway process can't exhaust your machine (that's the "don't break my
  computer" line, not a restriction on what he does).
- His home (``/home/kith``) persists in a named volume across restarts.

The module shells into the container via the ``docker`` CLI (no extra Python
dependency) and degrades to a clear error whenever Docker isn't running.
"""

from __future__ import annotations

import base64
import html
import json
import re
import shlex
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from kith import settings
from kith.infra import renderer

IMAGE = "kith-sandbox:latest"
CONTAINER = "kith-sandbox"
VOLUME = "kith-home"
HOME = "/home/kith"

# Web search goes through a SearXNG instance (keyless, private). Defaults to one
# reachable on the host at :8888; override with KITH_SEARCH_URL.
SEARCH_URL = settings.SEARCH_URL

_BUILD_CONTEXT = settings.SANDBOX_BUILD_DIR

# It's his machine — root inside, no artificial limits on what he does. The only
# caps here protect the *host*: generous resource ceilings so a runaway process
# can't take down your Mac, plus auto-restart. No host mounts, no Docker socket,
# not privileged — that's the wall around your computer.
_RUN_FLAGS = [
    "--restart=unless-stopped",
    "--user=root",
    "--env=HOME=/home/kith",
    "--memory=4g",
    "--memory-swap=4g",
    "--cpus=4",
    "--pids-limit=4096",
]
# How long a single *foreground* command may block. It's not a cap on what he can
# run — anything long-lived (servers, watchers, big builds) should be started in
# the background (`nohup … &`), which returns instantly and keeps running.
_EXEC_TIMEOUT = 900
_OUTPUT_LIMIT = 8_000  # chars of output returned to the model (protects context)
_MAX_WRITE = 5_000_000  # bytes a single write_file may write


class SandboxError(RuntimeError):
    """Anything that goes wrong reaching or using the sandbox."""


@dataclass
class ExecResult:
    exit_code: int
    output: str


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def docker_available() -> bool:
    try:
        return _docker(["info"], timeout=10).returncode == 0
    except SandboxError:
        return False


def status() -> dict:
    available = docker_available()
    return {
        "dockerAvailable": available,
        "imageBuilt": _image_exists() if available else False,
        "container": _container_state() if available else None,
    }


def ensure_ready() -> None:
    """Build the image (if needed) and make sure the container is running."""
    if not docker_available():
        raise SandboxError("Docker isn't running. Start Docker Desktop and try again.")
    if not _image_exists():
        build = _docker(["build", "-t", IMAGE, str(_BUILD_CONTEXT)], timeout=600)
        if build.returncode != 0:
            raise SandboxError(f"Failed to build sandbox image: {_tail(build.stderr)}")
    state = _container_state()
    if state is None:
        run = _docker(
            [
                "run",
                "-d",
                "--name",
                CONTAINER,
                *_RUN_FLAGS,
                "-v",
                f"{VOLUME}:{HOME}",
                "-w",
                HOME,
                IMAGE,
                "sleep",
                "infinity",
            ],
            timeout=90,
        )
        if run.returncode != 0:
            raise SandboxError(f"Failed to start sandbox: {_tail(run.stderr)}")
    elif state != "running":
        _docker(["start", CONTAINER], timeout=30)


def reset() -> None:
    """Remove the container and its home volume — a fresh machine next time."""
    _docker(["rm", "-f", CONTAINER], timeout=30)
    _docker(["volume", "rm", VOLUME], timeout=30)


# --------------------------------------------------------------------------- #
# Computer operations (all run inside the container)
# --------------------------------------------------------------------------- #


def run_command(command: str, timeout: int = _EXEC_TIMEOUT) -> ExecResult:
    ensure_ready()
    proc = _docker(["exec", CONTAINER, "bash", "-lc", command], timeout=timeout + 5)
    combined = proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")
    return ExecResult(exit_code=proc.returncode, output=_clip(combined))


_READ_DEFAULT_LINES = 400  # lines returned when a file is read without a range
_MAX_UI_READ = 2_000_000  # bytes the file viewer will pull in one go


def read_file(path: str, offset: int | None = None, limit: int | None = None) -> str:
    """Read a file, optionally just a line-range slice. Like Claude Code's Read:
    output is line-numbered so it composes with grep, and a large file returns a
    bounded window (with a hint to page on) rather than dumping the whole thing
    into context."""
    ensure_ready()
    target = resolve(path)
    start = max(1, offset or 1)
    count = limit if (limit and limit > 0) else _READ_DEFAULT_LINES
    end = start + count - 1
    # awk prints absolute line numbers for the window; wc gives the true length so
    # we can tell him there's more to page through.
    script = (
        f"total=$(wc -l < {shlex.quote(target)}); "
        f"awk 'NR>={start} && NR<={end} {{ printf \"%6d\\t%s\\n\", NR, $0 }}' {shlex.quote(target)}; "
        f'echo "@@TOTAL@@ $total"'
    )
    proc = _docker(["exec", CONTAINER, "bash", "-lc", script], timeout=30)
    if proc.returncode != 0:
        raise SandboxError(_tail(proc.stderr) or f"cannot read {path}")
    out = proc.stdout.decode(errors="replace")
    total = 0
    body_lines = []
    for line in out.splitlines():
        if line.startswith("@@TOTAL@@ "):
            try:
                total = int(line.split(" ", 1)[1].strip())
            except ValueError:
                total = 0
        else:
            body_lines.append(line)
    body = "\n".join(body_lines)
    if total > end:
        body += (
            f"\n… [showing lines {start}-{min(end, total)} of {total}; read with offset={end + 1} for more]"
        )
    return _clip(body)


def read_raw(path: str, max_bytes: int = _MAX_UI_READ) -> str:
    """The file exactly as it is — no line numbers, no window.

    `read_file` above is deliberately line-numbered and paged for *him*; the UI's
    file viewer needs the real bytes so Markdown renders and downloads match the
    file. Comes back base64 so nothing is mangled in transit.
    """
    ensure_ready()
    target = resolve(path)
    script = (
        f"size=$(stat -c %s {shlex.quote(target)}) || exit 1; "
        f'if [ "$size" -gt {int(max_bytes)} ]; then echo "@@TOOBIG@@ $size" >&2; exit 3; fi; '
        f"base64 -w0 {shlex.quote(target)}"
    )
    proc = _docker(["exec", CONTAINER, "bash", "-lc", script], timeout=60)
    if proc.returncode != 0:
        detail = _tail(proc.stderr)
        if "@@TOOBIG@@" in detail:
            size = detail.split("@@TOOBIG@@", 1)[1].strip()
            raise SandboxError(f"that file is too big to open here ({size} bytes; max {max_bytes})")
        raise SandboxError(detail or f"cannot read {path}")
    try:
        return base64.b64decode(proc.stdout).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SandboxError("that looks like a binary file, not text") from exc


def grep(pattern: str, path: str = ".", glob: str | None = None, max_matches: int = 60) -> str:
    """Search files for a pattern (ripgrep) and return matching lines with
    file:line numbers — so he can locate what he needs and then read just that
    slice, instead of loading whole files into context."""
    ensure_ready()
    target = resolve(path)
    args = ["rg", "--line-number", "--no-heading", "--color", "never", "--max-columns", "300"]
    if glob:
        args += ["--glob", glob]
    args += ["-e", pattern, target]
    # Cap the number of match lines so a broad pattern can't flood context.
    script = f"{shlex.join(args)} 2>/dev/null | head -n {int(max_matches)}"
    proc = _docker(["exec", CONTAINER, "bash", "-lc", script], timeout=30)
    out = proc.stdout.decode(errors="replace").strip()
    if not out:
        return f"No matches for {pattern!r} under {path}."
    lines = out.splitlines()
    tail = (
        f"\n… [showing first {max_matches} matches; narrow the pattern or set a path for the rest]"
        if len(lines) >= max_matches
        else ""
    )
    return _clip(out + tail)


def write_file(path: str, content: str) -> str:
    ensure_ready()
    data = content.encode()
    if len(data) > _MAX_WRITE:
        raise SandboxError(f"content too large ({len(data)} bytes; max {_MAX_WRITE})")
    target = resolve(path)
    encoded = base64.b64encode(data).decode()
    directory = shlex.quote(str(Path(target).parent))
    script = f"mkdir -p {directory} && printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(target)}"
    proc = _docker(["exec", CONTAINER, "bash", "-lc", script], timeout=30)
    if proc.returncode != 0:
        raise SandboxError(_tail(proc.stderr) or f"cannot write {path}")
    return f"wrote {len(data)} bytes to {target}"


def list_files(path: str = ".") -> str:
    ensure_ready()
    proc = _docker(["exec", CONTAINER, "bash", "-lc", f"ls -la {shlex.quote(resolve(path))}"], timeout=30)
    if proc.returncode != 0:
        raise SandboxError(_tail(proc.stderr) or f"cannot list {path}")
    return _clip(proc.stdout.decode(errors="replace"))


def list_dir(path: str = ".") -> list[dict]:
    """Structured one-level listing of his workspace, for the file browser."""
    ensure_ready()
    target = resolve(path)
    script = (
        f"find {shlex.quote(target)} -maxdepth 1 -mindepth 1 -printf '%y\\t%s\\t%P\\n' 2>/dev/null | sort"
    )
    proc = _docker(["exec", CONTAINER, "bash", "-lc", script], timeout=30)
    if proc.returncode != 0:
        raise SandboxError(_tail(proc.stderr) or f"cannot list {path}")
    entries = []
    for line in proc.stdout.decode(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        kind, size, name = parts
        entries.append(
            {
                "name": name,
                "type": "dir" if kind == "d" else "file",
                "size": int(size) if size.isdigit() else 0,
            }
        )
    return entries


def fetch_url(url: str) -> str:
    if not re.match(r"^https?://", url.strip()):
        raise SandboxError("url must start with http:// or https://")
    result = run_command(
        f"curl -sL --max-time 25 -A 'Mozilla/5.0 (Kith)' {shlex.quote(url.strip())}",
        timeout=30,
    )
    if result.exit_code != 0 and not result.output:
        raise SandboxError("fetch failed (is the sandbox online?)")
    return _html_to_text(result.output)


def browse_page(url: str) -> str:
    """Render a page in a real browser and return its visible text — for JS-heavy
    sites that fetch_url (plain curl) can't read.

    Prefers the desktop app's Chromium when it's running: it's already installed and
    already updated with Electron, so nothing extra has to be shipped or maintained.
    Falls back to Playwright inside his sandbox, which is what a headless server
    (Docker, `python app.py`) uses.

    A renderer that's registered but broken raises rather than silently falling
    back — a slow path quietly replacing the fast one is how "it got slower for no
    reason" starts.
    """
    target = url.strip()
    if not re.match(r"^https?://", target):
        raise SandboxError("url must start with http:// or https://")

    rendered = renderer.render(target)
    if rendered is not None:
        return _clip(rendered)

    result = run_command(f"python3 /opt/kith/browse.py {shlex.quote(target)}", timeout=75)
    if result.exit_code != 0 and not result.output.strip():
        raise SandboxError(
            f"couldn't render the page (it may have blocked the browser or timed out): {result.output[:200]}"
        )
    return _clip(result.output)


def _html_to_text(html: str) -> str:
    """Strip a fetched page down to readable text. Raw HTML is mostly tag noise
    that would otherwise eat context (and re-load every tool round). No parser
    dependency — a few regexes get us most of the way."""
    if "<" not in html:
        return _clip(html)
    text = re.sub(r"(?is)<(script|style|head|noscript|svg)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)  # drop remaining tags
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)  # collapse blank runs
    return _clip(text.strip())


def searx_search(query: str, limit: int = 5) -> list[dict]:
    """Search via a SearXNG instance (JSON API). Keyless and free, but it depends
    on public engines that CAPTCHA it, so this is one link in a chain — see
    ``websearch.search``. Returns [] when there are simply no hits, and raises
    with the reason when the instance itself is the problem."""
    encoded = urllib.parse.quote(query)
    result = run_command(
        # Short timeout on purpose: a blocked instance should hand off to the next
        # provider quickly rather than making him wait out a doomed query.
        f"curl -sL --max-time 10 '{SEARCH_URL}/search?q={encoded}&format=json'",
        timeout=15,
    )
    try:
        data = json.loads(result.output)
    except (ValueError, TypeError):
        raise SandboxError(
            f"SearXNG at {SEARCH_URL} did not return JSON (is it up, with the JSON format enabled?)"
        ) from None

    hits = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("content") or "")[:300],
        }
        for item in (data.get("results") or [])[:limit]
    ]
    if hits:
        return hits
    # It answered, but every engine behind it was blocked — that's a broken
    # instance masquerading as an empty web, and worth saying out loud.
    blocked = data.get("unresponsive_engines") or []
    if blocked:
        detail = ", ".join(
            f"{item[0]}: {item[1]}" for item in blocked if isinstance(item, list) and len(item) > 1
        )
        raise SandboxError(f"every SearXNG engine was blocked ({detail})")
    return []


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _docker(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["docker", *args], capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise SandboxError("The 'docker' command was not found. Is Docker installed?") from None
    except subprocess.TimeoutExpired:
        raise SandboxError("A docker command timed out.") from None


def _image_exists() -> bool:
    return _docker(["image", "inspect", IMAGE], timeout=15).returncode == 0


def _container_state() -> str | None:
    proc = _docker(["inspect", "-f", "{{.State.Status}}", CONTAINER], timeout=15)
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def resolve(path: str) -> str:
    """A bare relative path is anchored at Kith's home; absolute paths are kept."""
    p = (path or "").strip()
    if not p:
        return HOME
    return p if p.startswith("/") else f"{HOME}/{p}"


def _clip(text: str) -> str:
    if len(text) > _OUTPUT_LIMIT:
        return text[:_OUTPUT_LIMIT] + f"\n… [truncated, {len(text)} chars total]"
    return text


def _tail(stderr: bytes) -> str:
    return stderr.decode(errors="replace").strip()[-400:]


_RESULT_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)


def _parse_ddg(page: str, limit: int) -> list[dict]:
    links = _RESULT_RE.findall(page)
    snippets = _SNIPPET_RE.findall(page)
    out = []
    for index, (href, title) in enumerate(links[:limit]):
        out.append(
            {
                "title": _text(title),
                "url": _unwrap(href),
                "snippet": _text(snippets[index]) if index < len(snippets) else "",
            }
        )
    return out


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _unwrap(href: str) -> str:
    """DuckDuckGo wraps result links in a redirect; pull out the real URL."""
    match = re.search(r"[?&]uddg=([^&]+)", href)
    return urllib.parse.unquote(match.group(1)) if match else href


def kind_of(path: str) -> str:
    """``"file"``, ``"dir"``, or ``""`` when there's nothing there.

    Asked before copying, because a folder and a file are copied to different
    destinations — and because a website is a folder even when someone clicked the
    html inside it.
    """
    ensure_ready()
    target = resolve(path)
    script = f"if [ -d {shlex.quote(target)} ]; then echo dir; elif [ -e {shlex.quote(target)} ]; then echo file; fi"
    proc = _docker(["exec", CONTAINER, "bash", "-lc", script], timeout=20)
    return proc.stdout.decode(errors="replace").strip()


def copy_out(container_path: str, destination: Path) -> None:
    """Copy a file or a whole directory from his home out to the host.

    ``docker cp`` rather than ``cat``: it preserves the bytes exactly, which matters
    for what this exists for — a spreadsheet or an image the text viewer cannot show
    is precisely the reason someone wants it on their own machine — and it recurses,
    which is what makes handing over a project folder possible at all.
    """
    ensure_ready()
    proc = _docker(["cp", f"{CONTAINER}:{container_path}", str(destination)], timeout=300)
    if proc.returncode != 0:
        raise SandboxError(_tail(proc.stderr) or f"cannot copy {container_path}")
