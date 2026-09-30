/**
 * What one tool call's row says: how it ended, and the one fact worth reading without opening it.
 *
 * Every row in the thread has the same five slots — kind, verb, subject, digest, state — and this
 * file owns the last two. It is separate from `tool-language.ts` because that table is about the
 * *call* (what he did, to what) and is read by a server test that holds it to one entry per line;
 * this is about the *result*, which needs real logic per tool.
 *
 * Two jobs, kept apart on purpose:
 *
 * **`outcomeOf`** — did it work. The envelope's `ok: false` is only one of four ways a call can
 * fail to do its job. A permission refusal carries `permission`; a rule inside a tool answers
 * `ok: true` with `{blocked, next}` (update_task's "1 of 9 requirements not met" came back that
 * way, and rendered as a neutral card); a browser page that did not load answers a bare dict with
 * `failure` set. Each of those used to render as success, because only the first was looked for.
 *
 * **`digestOf`** — what came back, in a few words, *without repeating the subject*. The old
 * summary line put the command or the path back in front of the answer ("npm test · ok · 12
 * lines" under a row that already said "ran npm test"), so the only new information was the tail
 * and the row wrapped to show the part you had already read.
 */

import { unwrap } from "@/lib/tool-envelope";

export { shortPath } from "@/lib/tool-language";

type Args = Record<string, unknown>;
type Obj = Record<string, unknown>;

export type RowState = "done" | "failed" | "denied" | "refused";

export interface Outcome {
  state: RowState;
  /** The first line of what went wrong, for the line under the row. "" when it worked. */
  reason: string;
  /** Set when a retired name ran as its replacement: the name it ran as. */
  ranAs?: string;
}

const isObj = (value: unknown): value is Obj =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

/** The first line that says something, cut to fit under a row. */
export function firstLine(text: unknown, max = 140): string {
  const line =
    String(text ?? "")
      .split("\n")
      .map((one) => one.trim())
      .find(Boolean) ?? "";
  // A Python exception type in front of a sentence is the program talking, not the answer.
  const plain = line.replace(/^[A-Z][A-Za-z]*(Error|Exception):\s*/, "");
  return plain.length > max ? `${plain.slice(0, max - 1).trimEnd()}…` : plain;
}

const RETIRED_NOTE = /`([a-z_]+)` is now `([a-z_]+)`/;

export function outcomeOf(name: string, wrapped: unknown): Outcome {
  const outer = isObj(wrapped) ? wrapped : {};
  const { value, failed } = unwrap(wrapped);
  const text = typeof value === "string" ? value : "";
  const ranAs = RETIRED_NOTE.exec(
    isObj(value) && typeof value.note === "string" ? value.note : text,
  )?.[2];
  const done: Outcome = { state: "done", reason: "", ...(ranAs ? { ranAs } : {}) };

  if (failed) {
    const reason = firstLine(isObj(value) ? (value.message ?? JSON.stringify(value)) : value);
    if ("permission" in outer) return { state: "denied", reason };
    // Taken away for the landing reserve: not a failure of the tool, a rule about the moment.
    if (/is not available in this part of the turn/.test(reason)) {
      return { state: "refused", reason: "Not available now — he's wrapping up the turn." };
    }
    return { state: "failed", reason, ...(ranAs ? { ranAs } : {}) };
  }

  if (!isObj(value)) return done;

  // A rule inside the tool said no, and said so inside a successful envelope.
  if (typeof value.blocked === "string" && value.blocked) {
    return { state: "refused", reason: firstLine(value.blocked) };
  }
  if (value.ok === false && typeof value.error === "string") {
    return { state: "refused", reason: firstLine(value.error) };
  }
  // A plugin answers as itself, with its own failure field — and so does the browser.
  if ((name === "browser" || name.startsWith("plugin__")) && typeof value.failure === "string" && value.failure) {
    return { state: "failed", reason: firstLine(value.failure) };
  }
  // A tool that answered `{error}` and nothing else did not do its job.
  const keys = Object.keys(value).filter((key) => key !== "note");
  if (keys.length === 1 && keys[0] === "error" && typeof value.error === "string") {
    return { state: "failed", reason: firstLine(value.error) };
  }
  return done;
}

/* ── the digest ─────────────────────────────────────────────────────────── */

const s = (value: unknown) => (value == null ? "" : String(value));
const n = (value: unknown) => (typeof value === "number" ? value : Number(value) || 0);
const plural = (count: number, one: string, many = `${one}s`) =>
  `${count.toLocaleString()} ${count === 1 ? one : many}`;
const short = (value: unknown, max = 48) => {
  const text = s(value).replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
};
const lineCount = (text: string) => (text.trim() ? text.replace(/\n$/, "").split("\n").length : 0);

/** `+12 −3`, counted from a unified diff. Header lines (`+++`, `---`) are not changes. */
export function diffStat(diff: string): { plus: number; minus: number } {
  return {
    plus: (diff.match(/^\+(?!\+\+)/gm) || []).length,
    minus: (diff.match(/^-(?!--)/gm) || []).length,
  };
}

const stat = (diff: string) => {
  const { plus, minus } = diffStat(diff);
  return `+${plus} −${minus}`;
};

/** "3 files changed, 216 insertions(+), 83 deletions(-)" → "3 files · +216 −83". */
export function gitShortstat(text: string): string {
  const files = /(\d+) files? changed/.exec(text)?.[1];
  const plus = /(\d+) insertions?\(\+\)/.exec(text)?.[1] ?? "0";
  const minus = /(\d+) deletions?\(-\)/.exec(text)?.[1] ?? "0";
  if (!files) return short(text);
  return `${plural(Number(files), "file")} · +${plus} −${minus}`;
}

function browserDigest(command: string, r: Obj): string {
  switch (command) {
    case "open":
      return short(r.title) || "loaded";
    case "read":
      return plural(lineCount(s(r.text)), "line");
    case "click":
      return r.clicked ? "" : "nothing to click";
    case "look":
      return r.width ? `screenshot ${s(r.width)}×${s(r.height)}` : "looked";
    case "resize":
      return r.width ? `${s(r.width)} × ${s(r.height)}` : "";
    case "type":
      return short(r.into);
    case "press":
      return short(r.did);
    case "scroll":
      return typeof r.moved === "number" ? (r.moved ? `${Math.abs(r.moved)}px` : "at the end") : "";
    case "tabs":
    case "close":
      return Array.isArray(r.tabs) ? plural(r.tabs.filter((one) => one !== "no tabs are open").length, "tab") : "";
    case "switch":
      return short(r.title);
    case "wait":
      return short(r.waited);
    case "errors":
      return Array.isArray(r.errors) ? plural(r.errors.length, "error") : "none";
    case "save_test":
      return short(r.saved);
    case "show":
      return r.opened ? "opened" : "";
    default:
      return "";
  }
}

/** The one useful fact about what came back, or "" when the row already says it all. */
export function digestOf(name: string, args: Args, wrapped: unknown): string {
  const { value, failed } = unwrap(wrapped);
  if (failed) return "";
  const r: Obj = isObj(value) ? value : {};
  const text = typeof value === "string" ? value : "";
  const list = Array.isArray(value) ? value : null;

  if (name === "browser") return browserDigest(String(args?.act ?? ""), r);
  if (name.startsWith("plugin__")) {
    const [, plugin = "", command = ""] = /^plugin__(.+?)__(.+)$/.exec(name) ?? [];
    if (plugin === "browser") return browserDigest(command, r);
    if (typeof r.note === "string") return short(r.note);
    if (isObj(r.changed)) return plural(Object.keys(r.changed).length, "change");
    if (r.queued) return "queued";
    return "";
  }
  if (name.startsWith("mcp__")) {
    const lines = lineCount(text);
    return lines > 1 ? plural(lines, "line") : short(text);
  }

  switch (name) {
    case "shell": {
      const code = n(r.exitCode);
      const lines = lineCount(s(r.output));
      return `exit ${code} · ${lines ? plural(lines, "line") : "no output"}`;
    }
    case "edit_files":
    case "edit_file": {
      const diff = s(r.diff ?? text);
      const files = n(r.files);
      return files > 1 ? `${stat(diff)} · ${plural(files, "file")}` : stat(diff);
    }
    case "write_file":
      return plural(lineCount(s(args.content)), "line");
    case "read_file": {
      if (r.image || r.data) return "looked at it";
      if (Array.isArray(r.images)) return plural(r.images.length, "picture");
      if (r.note) return short(r.note, 40);
      const range = /lines (\d+)-(\d+) of (\d+)/.exec(text.split("\n", 1)[0] ?? "");
      if (range) return `lines ${range[1]}–${range[2]} of ${range[3]}`;
      return plural(lineCount(text), "line");
    }
    case "delete_file":
      return "to the Trash";
    case "grep": {
      const hits = text.split("\n").filter((line) => /^[^\s=].*?:\d+:/.test(line)).length;
      return hits ? plural(hits, "match", "matches") : "no matches";
    }
    case "glob":
    case "list_files": {
      if (/^Nothing under/.test(text)) return "nothing matches";
      const rows = text.split("\n").filter((line) => line.trim() && !/^under /.test(line));
      return plural(rows.length, "file");
    }
    case "check_code":
    case "diagnostics": {
      if (r.checked === false) return "no checker here";
      if (r.clean === true) return r.checker ? `clean · ${s(r.checker)}` : "clean";
      if (typeof r.errors === "number" || typeof r.warnings === "number") {
        const parts = [
          n(r.errors) ? plural(n(r.errors), "error") : "",
          n(r.warnings) ? plural(n(r.warnings), "warning") : "",
        ].filter(Boolean);
        return parts.join(" · ") || "clean";
      }
      const problems = lineCount(s(r.problems));
      return problems ? plural(problems, "problem") : "clean";
    }
    case "changes":
    case "history": {
      if (/^Nothing has changed/.test(text)) return "nothing uncommitted";
      const files = (text.match(/^diff --git/gm) || []).length;
      if (files) return `${plural(files, "file")} · ${stat(text)}`;
      const commits = text.split("\n").filter((line) => /^[0-9a-f]{7,40}\b/.test(line)).length;
      return commits ? plural(commits, "commit") : "";
    }
    case "commit":
      return r.committed === false ? "nothing to record" : gitShortstat(s(r.changed));
    case "publish":
    case "check_remote":
      return short(r.out ?? r.in ?? r.remote);
    case "repo_map":
    case "outline":
      if (typeof r.map === "string") return `${s(r.filesShown)} of ${plural(n(r.filesFound), "file")}`;
      return typeof r.definitions === "number" ? plural(r.definitions, "definition") : "";
    case "find_symbol":
    case "references":
    case "definition": {
      const defs = Array.isArray(r.definitions) ? r.definitions.length : 0;
      const uses = Array.isArray(r.references) ? r.references.length : 0;
      if (!defs && !uses) return "not found";
      return `${plural(defs, "definition")} · ${plural(uses, "use")}`;
    }
    case "rename_symbol":
      return n(r.renamed)
        ? `${plural(n(r.renamed), "place")} · ${plural(Array.isArray(r.files) ? r.files.length : 0, "file")}`
        : "nothing renamed";
    case "install_language_support":
      if (Array.isArray(r.needed)) return `needs ${r.needed.map(String).join(", ")}`;
      return r.installed ? "installed" : "";
    case "run_tests": {
      if (r.status === "running") return `still running · ${s(r.for)}`;
      const parts = [
        n(r.passed) ? `${n(r.passed)} passed` : "",
        n(r.failed) ? `${n(r.failed)} failed` : "",
        n(r.errors) ? plural(n(r.errors), "error") : "",
        n(r.skipped) ? `${n(r.skipped)} skipped` : "",
      ].filter(Boolean);
      return parts.join(" · ") || (r.ok ? "passed" : "");
    }
    case "start_process":
    case "check_process": {
      if (Array.isArray(r.running)) {
        const alive = r.running.filter((one) => isObj(one) && one.alive).length;
        return `${alive} running`;
      }
      const quiet = /nothing new/.test(s(r.note)) ? " · nothing new" : "";
      if (r.alive) return `running · ${s(r.for)}${quiet}`;
      return r.exitCode !== undefined ? `exited ${s(r.exitCode)}` : "stopped";
    }
    case "stop_process":
      return r.ranFor ? `after ${s(r.ranFor)}` : "";
    case "web_search": {
      if (!list) return "";
      if (list.length && list.every((hit) => isObj(hit) && hit.note && !hit.url)) return "search is down";
      return list.length ? plural(list.length, "result") : "nothing found";
    }
    case "browse_page":
    case "fetch_url":
      return r.how === "rendered in a browser" ? "rendered in a browser" : r.how ? "downloaded" : "";
    case "search_sources":
      return list ? (list.length ? plural(list.length, "hit") : "nothing found") : "";
    case "read_source":
      return short(r.title ?? r.note);
    case "remember":
      return r.level ? `${s(r.level)} · importance ${s(r.importance)}` : "";
    case "recall":
      return list ? (list.length ? plural(list.length, "memory", "memories") : "nothing") : "";
    case "forget":
      return r.forgotten === false ? "not found" : "";
    case "set_memory_level":
      return s(r.level);
    case "read_journal":
      return typeof r.total === "number" ? plural(r.total, "entry", "entries") : "";
    case "add_task":
      if (r.duplicate) return "already there";
      return r.id ? `#${s(r.id)} · ${s(r.status)}` : "";
    case "list_tasks":
    case "view_task":
      if (typeof r.total === "number") return plural(r.total, "task");
      if (Array.isArray(r.items)) return plural(r.items.length, "task");
      return r.id ? `#${s(r.id)} · ${s(r.status)}` : "";
    case "update_task":
      return r.id ? (args.status ? `#${s(r.id)} → ${s(r.status)}` : `#${s(r.id)}`) : "";
    case "plan_work": {
      const tasks = Array.isArray(r.tasks) ? r.tasks.length : 0;
      const refused = Array.isArray(r.refused) ? r.refused.length : 0;
      return [tasks ? plural(tasks, "task") : "", refused ? `${refused} refused` : ""]
        .filter(Boolean)
        .join(" · ");
    }
    case "checklist":
    case "add_checklist_item":
    case "check_item": {
      if (Array.isArray(r.added) || Array.isArray(r.checked)) {
        const added = Array.isArray(r.added) ? r.added.length : 0;
        const ticked = Array.isArray(r.checked) ? r.checked.length : 0;
        return [added ? `added ${added}` : "", ticked ? `ticked ${ticked}` : ""].filter(Boolean).join(" · ");
      }
      return r.done ? "ticked" : r.text ? "added" : "";
    }
    case "add_deliverable":
      return s(r.kind);
    case "show_canvas":
    case "show_board":
      return r.shown ? plural(Array.isArray(r.designs) ? r.designs.length : 0, "design") : "nothing to show";
    case "create_project":
      return r.id ? `#${s(r.id)}` : "";
    case "list_projects":
      return typeof r.total === "number" ? plural(r.total, "project") : "";
    case "update_project":
      return s(r.status);
    case "schedule":
    case "set_reminder":
      return short(r.fires);
    case "list_schedules":
    case "list_reminders": {
      const standing = n(r.standing);
      const once = typeof r.total === "number" ? r.total : 0;
      return [standing ? `${standing} standing` : "", once ? `${once} once` : ""].filter(Boolean).join(" · ") || "nothing waiting";
    }
    case "cancel_schedule":
    case "cancel_reminder":
      return r.cancelled ? "cancelled" : "not found";
    case "ask": {
      if (r.answered === false) return "no answer";
      const answers = Array.isArray(r.answers) ? r.answers : [];
      if (!answers.length) return "";
      return answers.every((one) => isObj(one) && one.skipped) ? "you skipped it" : "answered";
    }
    case "reach_out":
      return r.sent ? "in Alerts" : "";
    case "read_skill":
      return r.instructions ? "" : "already open";
    case "read_design_system":
      if (r.system === "none") return "none yet";
      return isObj(r.colors) ? `${s(r.name)} · ${plural(Object.keys(r.colors).length, "colour")}` : s(r.name);
    case "update_design_system": {
      const colours = Array.isArray(r.colors) ? r.colors.length : 0;
      const components = Array.isArray(r.components) ? r.components.length : 0;
      return [colours ? plural(colours, "colour") : "", components ? plural(components, "component") : ""]
        .filter(Boolean)
        .join(" · ");
    }
    case "find_icons": {
      const icons = Array.isArray(r.icons) ? (r.icons as unknown[]).filter(isObj) : [];
      const found = icons.filter((one) => one.icon).length;
      const missing = icons.length - found;
      return [plural(found, "icon"), missing ? `${missing} missing` : ""].filter(Boolean).join(" · ");
    }
    case "run_workflow": {
      const phases = Array.isArray(r.phases) ? r.phases.map(String) : [];
      return phases.join(" → ");
    }
    default:
      return generic(value);
  }
}

/** For a tool nobody has written a digest for: a count, or the one value worth printing. */
function generic(value: unknown): string {
  if (Array.isArray(value)) return value.length ? plural(value.length, "item") : "nothing";
  if (typeof value === "string") return value.trim() ? plural(lineCount(value), "line") : "nothing";
  if (isObj(value)) {
    if (typeof value.total === "number") return plural(value.total, "item");
    for (const key of ["note", "status", "message"]) {
      if (typeof value[key] === "string" && value[key]) return short(value[key]);
    }
  }
  return "";
}

/* ── diffs ─────────────────────────────────────────────────────────────── */

export interface FileDiff {
  path: string;
  lines: string[];
}

/** Split `edit_files`' combined diff into one section per file. Exported to be tested: it is a
 *  parser over text the server writes, and getting it wrong silently drops a file's change. */
export function splitDiff(diff: string): FileDiff[] {
  const files: FileDiff[] = [];
  let current: FileDiff | null = null;
  for (const line of diff.split("\n")) {
    const after = /^\+\+\+ (?:b\/)?(.+?)(?: \(after\))?$/.exec(line);
    if (after) {
      current = { path: after[1] ?? "", lines: [] };
      files.push(current);
      continue;
    }
    // The `---` twin of the header, a git `diff --git`/`index` line, and the server's own
    // "1 replacement in <path>" caption: all bookkeeping the section heading already says.
    if (/^--- /.test(line) || /^diff --git /.test(line) || /^index [0-9a-f]/.test(line)) continue;
    if (/^\d+ replacements? in /.test(line)) continue;
    if (current) current.lines.push(line);
  }
  while (files.length && files.at(-1)?.lines.at(-1) === "") files.at(-1)?.lines.pop();
  return files;
}
