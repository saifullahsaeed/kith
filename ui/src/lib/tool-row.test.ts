import { describe, expect, it } from "vitest";

import { describeCall } from "@/lib/tool-language";
import { digestOf, gitShortstat, outcomeOf, shortPath, splitDiff } from "@/lib/tool-row";

/**
 * Every sample here is a real result from his transcripts (server/data/conversations), trimmed.
 * The row is the thing he reads a hundred of per turn, so what it says is pinned per tool.
 */

const ok = (result: unknown) => ({ ok: true, result });

describe("how a call ended", () => {
  it("reads a failed envelope as failed, with the first line and no exception type", () => {
    const outcome = outcomeOf("glob", {
      ok: false,
      error: "WorkspaceError: /Users/saifullah/Kith is not a folder to search in",
    });
    expect(outcome).toEqual({ state: "failed", reason: "/Users/saifullah/Kith is not a folder to search in" });
  });

  it("tells a permission refusal from a failure", () => {
    const outcome = outcomeOf("shell", { ok: false, error: "He wants to run a command…", permission: { kind: "run" } });
    expect(outcome.state).toBe("denied");
  });

  it("sees a rule saying no inside a successful envelope", () => {
    // update_task refusing "done": this rendered as a neutral card, as if it had worked.
    const outcome = outcomeOf(
      "update_task",
      ok({ blocked: "1 of 9 requirements not met by your own account, so this is not done.", not_met: ["native app"] }),
    );
    expect(outcome).toEqual({ state: "refused", reason: "1 of 9 requirements not met by your own account, so this is not done." });
  });

  it("reads the landing reserve as a rule about the moment, not a broken tool", () => {
    const outcome = outcomeOf("web_search", {
      ok: false,
      error: "`web_search` is not available in this part of the turn — land what you have.",
    });
    expect(outcome.state).toBe("refused");
  });

  it("sees a browser page that did not load, though the plugin answered", () => {
    const outcome = outcomeOf("plugin__browser__open", { url: "http://x", failure: "net::ERR_CONNECTION_REFUSED" });
    expect(outcome).toEqual({ state: "failed", reason: "net::ERR_CONNECTION_REFUSED" });
  });

  it("names what a retired tool ran as", () => {
    const outcome = outcomeOf("view_task", ok({ id: 1, note: "`view_task` is now `list_tasks` — same job, so this ran as that." }));
    expect(outcome).toEqual({ state: "done", reason: "", ranAs: "list_tasks" });
  });

  it("leaves an ordinary success alone", () => {
    expect(outcomeOf("shell", ok({ exitCode: 1, output: "x" })).state).toBe("done");
  });
});

describe("the one fact on each row", () => {
  it("never repeats the subject", () => {
    const digest = digestOf("shell", { command: "npm test" }, ok({ exitCode: 2, output: "a\nb\nc" }));
    expect(digest).toBe("exit 2 · 3 lines");
    expect(digest).not.toContain("npm test");
  });

  it("counts an edit's lines and files", () => {
    const diff = "--- a (before)\n+++ a (after)\n@@ -1 +1 @@\n-old\n+new\n+more";
    expect(digestOf("edit_files", {}, ok({ files: 1, diff }))).toBe("+2 −1");
    expect(digestOf("edit_files", {}, ok({ files: 3, diff }))).toBe("+2 −1 · 3 files");
  });

  it("says what check_code found — it used to say '1 line to fix' for everything", () => {
    expect(
      digestOf("check_code", {}, ok({ engine: "language server", clean: false, errors: 2, warnings: 0, problems: [{}, {}] })),
    ).toBe("2 errors");
    expect(digestOf("check_code", {}, ok({ ran: "tsc", checker: "tsc", clean: true, problems: "" }))).toBe("clean · tsc");
  });

  it("puts a commit's stat in numbers", () => {
    expect(gitShortstat("3 files changed, 216 insertions(+), 83 deletions(-)")).toBe("3 files · +216 −83");
    expect(digestOf("commit", {}, ok({ committed: false, note: "x" }))).toBe("nothing to record");
  });

  it("reads test runs, finished and not", () => {
    expect(digestOf("run_tests", {}, ok({ runner: "pytest", ok: true, passed: 19 }))).toBe("19 passed");
    expect(digestOf("run_tests", {}, ok({ passed: 7, failed: 1 }))).toBe("7 passed · 1 failed");
    expect(digestOf("run_tests", {}, ok({ status: "running", for: "20s" }))).toBe("still running · 20s");
  });

  it("reads a background process", () => {
    expect(
      digestOf("check_process", {}, ok({ alive: true, for: "59s", note: "still running; nothing new since you last looked" })),
    ).toBe("running · 59s · nothing new");
    expect(digestOf("stop_process", {}, ok({ stopped: "x", ranFor: "2m" }))).toBe("after 2m");
  });

  it("gives the browser's answers words", () => {
    expect(digestOf("plugin__browser__open", {}, { url: "u", title: "Sadeef — AI-native ERP" })).toBe("Sadeef — AI-native ERP");
    expect(digestOf("plugin__browser__look", {}, { width: 1056, height: 660, shot: "/x.png" })).toBe("screenshot 1056×660");
    expect(digestOf("plugin__browser__click", { text: "Issue" }, { clicked: "", options: "Log in" })).toBe("nothing to click");
  });

  it("says when search itself is down rather than 1 item", () => {
    expect(digestOf("web_search", {}, ok([{ note: "every engine refused" }]))).toBe("search is down");
    expect(digestOf("web_search", {}, ok([{ title: "a", url: "b", snippet: "c" }]))).toBe("1 result");
  });

  it("counts grep matches and ignores pattern headers", () => {
    const text = "===== foo =====\na.py:3:foo\nb.py:9:  foo()\n";
    expect(digestOf("grep", {}, ok(text))).toBe("2 matches");
  });

  it("reads a glob that found nothing", () => {
    expect(digestOf("glob", {}, ok("Nothing under ~/Kith/trials matches **/*.{md,txt}"))).toBe("nothing matches");
  });
});

describe("the row's subject", () => {
  it("reads project paths as relative, home as ~", () => {
    const root = "/Users/saif/Desktop/personal/Asterion";
    expect(shortPath(`${root}/webapp/static/app.js`, root)).toBe("webapp/static/app.js");
    expect(shortPath("/Users/saif/Kith/notes.md", root)).toBe("~/Kith/notes.md");
    const call = describeCall("read_file", { paths: [`${root}/webapp/app.py`] }, root);
    expect(call.subject).toBe("webapp/app.py");
  });

  it("names edit_files' files, which live inside the edits", () => {
    const call = describeCall("edit_files", { edits: [{ path: "/Users/s/p/a.ts" }, { path: "/Users/s/p/a.ts" }, { path: "/Users/s/p/b.ts" }] }, "/Users/s/p");
    expect(call.subject).toBe("a.ts, b.ts");
  });

  it("says browser commands in words, not as a function name", () => {
    const call = describeCall("plugin__browser__open", { url: "http://localhost:5173/apps" });
    expect(call.verb).toBe("opened");
    expect(call.subject).toBe("http://localhost:5173/apps");
    expect(describeCall("plugin__browser__look", {}).verb).toBe("looked at the page");
  });

  it("gives an MCP tool its server as a badge", () => {
    const call = describeCall("mcp__github__create_pr", { title: "Fix it" });
    expect(call.verb).toBe("create pr");
    expect(call.badge).toBe("Github");
    expect(call.verb).not.toContain("mcp");
  });

  it("says a running call in the present tense", () => {
    expect(describeCall("shell", { command: "ls" }).doing).toBe("running");
    expect(describeCall("web_search", { query: "x" }).doing).toBe("searching the web for");
    expect(describeCall("run_tests", {}).doing).toBe("running the tests in");
  });
});

describe("splitting edit_files' diff by file", () => {
  it("makes one section per file and drops the headers", () => {
    const diff = [
      "1 replacement in /p/a.py",
      "--- /p/a.py (before)",
      "+++ /p/a.py (after)",
      "@@ -1,2 +1,2 @@",
      "-x = 1",
      "+x = 2",
      "1 replacement in /p/b.py",
      "--- /p/b.py (before)",
      "+++ /p/b.py (after)",
      "@@ -5 +5 @@",
      "+y",
      "",
    ].join("\n");
    const files = splitDiff(diff);
    expect(files.map((one) => one.path)).toEqual(["/p/a.py", "/p/b.py"]);
    expect(files[0]?.lines).toEqual(["@@ -1,2 +1,2 @@", "-x = 1", "+x = 2"]);
    expect(files[1]?.lines).toEqual(["@@ -5 +5 @@", "+y"]);
  });
});
