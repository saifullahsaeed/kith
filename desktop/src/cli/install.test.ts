/**
 * The states `kith install` can be in, and the quoting that puts it there.
 *
 * Written after being asked whether any of this was tested, and the honest answer for the
 * install half was no — it typechecked, and typechecking proves the wiring is named correctly
 * and nothing about whether it works. The claim these replace is the same kind of claim that
 * was already wrong once in this feature: that `~/.local/bin` is on PATH by default, asserted
 * from memory, contradicted by the machine it was written on.
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { linkScript, statusFrom } from "./install";

let room: string;
let link: string;
let target: string;

beforeEach(() => {
  room = fs.mkdtempSync(path.join(os.tmpdir(), "kith-cli-"));
  link = path.join(room, "bin", "kith");
  target = path.join(room, "Kith.app", "Resources", "kith");
  fs.mkdirSync(path.dirname(link), { recursive: true });
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "#!/bin/sh\necho hi\n", { mode: 0o755 });
});

afterEach(() => {
  fs.rmSync(room, { recursive: true, force: true });
});

describe("statusFrom", () => {
  it("is not installed when nothing is at the link", () => {
    const status = statusFrom(target, link);
    expect(status).toMatchObject({ available: true, installed: false, stale: false });
    expect(status.link).toBe(link);
  });

  it("is installed when the link points at our binary", () => {
    fs.symlinkSync(target, link);
    expect(statusFrom(target, link)).toMatchObject({ installed: true, stale: false });
  });

  it("is stale when the link points at a different Kith", () => {
    // The case an app update or a moved bundle leaves behind. It must not read as "not
    // installed": the fix is replacing the link, and telling somebody it is absent sends them
    // looking for a command that is right there and broken.
    const older = path.join(room, "Kith old.app", "Resources", "kith");
    fs.mkdirSync(path.dirname(older), { recursive: true });
    fs.writeFileSync(older, "", { mode: 0o755 });
    fs.symlinkSync(older, link);
    expect(statusFrom(target, link)).toMatchObject({ installed: false, stale: true });
  });

  it("is stale when the link dangles", () => {
    fs.symlinkSync(path.join(room, "deleted", "kith"), link);
    expect(statusFrom(target, link)).toMatchObject({ installed: false, stale: true });
  });

  it("leaves somebody else's real kith alone rather than calling it ours", () => {
    // A regular file at /usr/local/bin/kith is another tool with our name — a Homebrew
    // formula, something hand-written. `readlink` fails on it, and reporting "not installed"
    // is right: we must not quietly replace a file we did not create.
    fs.writeFileSync(link, "#!/bin/sh\n");
    expect(statusFrom(target, link)).toMatchObject({ installed: false, stale: false });
  });

  it("is unavailable when this build ships no binary", () => {
    // A checkout that has not run the freeze. The page must explain rather than offer a
    // button that cannot work.
    expect(statusFrom(null, link)).toMatchObject({ available: false, installed: false });
  });

  it("does not call a link installed just because the paths look alike", () => {
    fs.symlinkSync(`${target}-2`, link);
    expect(statusFrom(target, link)).toMatchObject({ installed: false, stale: true });
  });
});

describe("linkScript", () => {
  it("survives spaces in both paths", () => {
    // "/Applications/Kith 2.app" is an ordinary place for a second copy to live, and an
    // unquoted path there links to a directory that does not exist.
    const script = linkScript("/Applications/Kith 2.app/Contents/Resources/kith-cli/kith", "/usr/local/bin/kith");
    expect(script).toContain("'/Applications/Kith 2.app/Contents/Resources/kith-cli/kith'");
    expect(script).toContain("with administrator privileges");
  });

  it("creates the destination directory in the same elevated command", () => {
    // /usr/local/bin does not exist on a clean Mac that has never had Homebrew. Discovering
    // that after the password has been spent is a second prompt for one job.
    expect(linkScript("/a/kith", "/usr/local/bin/kith")).toContain("mkdir -p '/usr/local/bin'");
  });

  it("escapes a double quote rather than ending the AppleScript string on it", () => {
    const script = linkScript('/tmp/od"d/kith', "/usr/local/bin/kith");
    // The quote is escaped, so the string literal still terminates where it should: exactly
    // once, at the end, before `with administrator privileges`.
    expect(script).toContain('od\\"d');
    expect(script.match(/(?<!\\)"/g)).toHaveLength(2);
  });
});
