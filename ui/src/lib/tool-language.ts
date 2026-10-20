/**
 * What each of Kith's tools did, in English, with an icon for the kind of work it was.
 *
 * This lived inside what is now work-panel.tsx, which meant the Work feed said "read ~/Kith/cv.pdf" while
 * the chat thread — the bigger, louder surface, describing the very same call — said "1 tool
 * call", and said "Used tool: read_skill" when you opened it. The good vocabulary was in the
 * quiet panel and the raw one was in the prominent panel. They are the same events, so they
 * get the same words, and a shared table is the only way that stays true.
 *
 * `server/tests/test_mind_feed.py` reads this file: every registered tool must have a phrase,
 * every phrase must name a tool that exists, every `of:` must name a real argument, and every
 * `group:` must be declared. Keep both tables one entry per line — the test's patterns are
 * anchored on that shape.
 */

import {
  AlarmClock,
  BookOpenText,
  Brain,
  FileCode2,
  FolderKanban,
  Globe,
  Heart,
  ListChecks,
  Network,
  NotebookPen,
  Puzzle,
  Search,
  Send,
  TerminalSquare,
  Wrench,
  type LucideIcon,
} from "lucide-react";

/**
 * What kind of thing a tool touched, and the icon for it.
 *
 * The point is scanning rather than decoration. A column of identical arrows tells you
 * nothing; a column where six rows carry a terminal and two carry a globe tells you at a
 * glance that he spent this step building and briefly looked something up — which is the
 * question you actually have when you glance at a turn.
 *
 * `noun`/`one` are what a count of them is called, for a collapsed run of calls: "3 files · 2
 * commands" rather than "5 tool calls", which is a number about the machine instead of a
 * sentence about the work. Both forms are written out because English does not derive them —
 * "searches" cut to "searche" is worse than no summary at all.
 */
/** Named rather than written inline so the table below can keep one entry per line at two
 *  spaces of indent, which is the shape `test_mind_feed.py` matches on. */
interface WorkKind {
  icon: LucideIcon;
  tone: string;
  noun: string;
  one: string;
}

export const GROUP: Record<string, WorkKind> = {
  memory: { icon: Brain, tone: "text-violet-400/80", noun: "memories", one: "memory" },
  writing: { icon: NotebookPen, tone: "text-kith/80", noun: "notes", one: "note" },
  reading: { icon: BookOpenText, tone: "text-kith/70", noun: "reads", one: "read" },
  tasks: { icon: ListChecks, tone: "text-blue-400/80", noun: "tasks", one: "task" },
  projects: { icon: FolderKanban, tone: "text-blue-400/70", noun: "projects", one: "project" },
  self: { icon: Heart, tone: "text-pink-400/70", noun: "notes to self", one: "note to self" },
  time: { icon: AlarmClock, tone: "text-orange-400/80", noun: "reminders", one: "reminder" },
  outreach: { icon: Send, tone: "text-pink-400/80", noun: "messages", one: "message" },
  web: { icon: Globe, tone: "text-sky-400/80", noun: "pages", one: "page" },
  shell: { icon: TerminalSquare, tone: "text-emerald-400/80", noun: "commands", one: "command" },
  files: { icon: FileCode2, tone: "text-amber-400/80", noun: "files", one: "file" },
  search: { icon: Search, tone: "text-sky-400/70", noun: "searches", one: "search" },
  tools: { icon: Wrench, tone: "text-muted-foreground/70", noun: "tools", one: "tool" },
  skills: { icon: Puzzle, tone: "text-kith/80", noun: "skills", one: "skill" },
  // Its own kind rather than another "tool". Each of these starts, steers or ends whole
  // conversations running beside this one, and folding it into "7 tools" would hide the part worth
  // knowing. Teal, the colour agents wear everywhere else.
  agents: { icon: Network, tone: "text-teal-400/80", noun: "calls to agents", one: "call to agents" },
};

/**
 * What each tool did, and to what.
 *
 * `verb` is the phrase; `of` names the argument that is the subject of it. Both halves matter
 * and only the first existed: every call was rendered by splitting the raw text on "(" and
 * looking up the bare name, so a whole afternoon of work read as "read a file / wrote a file /
 * ran a command" over and over, with the one useful piece of information — *which* file,
 * *which* command — discarded on the way in.
 *
 * The table is exhaustive on purpose, and a test asserts it stays that way. Seventeen of the
 * fifty-five tools had no entry and fell through to the raw `name(arg=value)` string, so the
 * feed was half plain English and half code, and which half you got depended on which tool he
 * happened to reach for. A missing entry should fail the suite, not quietly print a function
 * call at someone.
 */
export const TOOL: Record<string, { verb: string; of?: string; group: keyof typeof GROUP }> = {
  publish: { verb: "exchanged with the remote", of: "direction", group: "files" },
  check_remote: { verb: "checked the remote", group: "files" },
  take_in_shared_tasks: { verb: "took in shared tasks", group: "tasks" },
  // memory and notes
  recall: { verb: "searched his memory for", of: "query", group: "memory" },
  remember: { verb: "remembered", of: "content", group: "memory" },
  forget: { verb: "let go of a memory", group: "memory" },
  set_memory_level: { verb: "re-shelved a memory", group: "memory" },
  journal: { verb: "wrote in his journal", group: "writing" },
  read_journal: { verb: "re-read his journal", group: "reading" },
  // tasks and projects
  add_task: { verb: "set himself", of: "goal", group: "tasks" },
  list_tasks: { verb: "looked over his tasks", group: "tasks" },
  update_task: { verb: "updated a task", group: "tasks" },
  // `view_task` folded into `list_tasks(ids:)`. Kept so transcripts from before the merge
  // still render — `conversations.full_messages` replays old tool calls forever.
  view_task: { verb: "opened task", of: "id", group: "tasks" },
  checklist: { verb: "worked the checklist", group: "tasks" },
  // The two names `checklist` replaced. Kept because `conversations.full_messages` replays old
  // tool calls on every resumed turn, so a transcript from before the merge still renders.
  add_checklist_item: { verb: "added a step", of: "text", group: "tasks" },
  check_item: { verb: "ticked off a step", group: "tasks" },
  add_deliverable: { verb: "handed over", of: "title", group: "tasks" },
  show_canvas: { verb: "opened the canvas", group: "tasks" },
  // The name `show_canvas` replaced, kept so older transcripts still render.
  show_board: { verb: "opened the canvas", group: "tasks" },
  create_project: { verb: "started the project", of: "name", group: "projects" },
  list_projects: { verb: "looked over his projects", group: "projects" },
  update_project: { verb: "updated a project", group: "projects" },
  // Milestones are gone. Their phrases stay, like every retired tool's, so older transcripts
  // still read as English.
  add_milestone: { verb: "added the milestone", of: "title", group: "projects" },
  update_milestone: { verb: "updated a milestone", group: "projects" },
  order_milestones: { verb: "put the milestones in order", group: "projects" },
  unlink_milestones: { verb: "unlinked two milestones", group: "projects" },
  link_folder: { verb: "linked a folder", of: "folder", group: "projects" },
  // time
  set_reminder: { verb: "set a reminder", of: "note", group: "time" },
  list_reminders: { verb: "checked his reminders", group: "time" },
  cancel_reminder: { verb: "cleared a reminder", group: "time" },
  schedule: { verb: "set something to come back to him", of: "note", group: "time" },
  list_schedules: { verb: "checked what he had waiting", group: "time" },
  cancel_schedule: { verb: "cleared something he had set", group: "time" },
  // reaching out
  reach_out: { verb: "reached out to you", group: "outreach" },
  // Present tense, alone in this table, and deliberately: every other phrase describes
  // something already done, while this one is still happening — the turn is parked on it
  // until you answer.
  ask: { verb: "is asking you", group: "outreach" },
  // knowledge and the web
  search_sources: { verb: "searched your sources for", of: "query", group: "search" },
  read_source: { verb: "read a source you gave him", group: "reading" },
  web_search: { verb: "searched the web for", of: "query", group: "search" },
  fetch_url: { verb: "read", of: "url", group: "web" },
  browse_page: { verb: "opened", of: "url", group: "web" },
  // The Browser tab. One tool with the act as an argument, so the row's words come from
  // `BROWSER_VERB` by act — this is only the fallback for a call with none.
  browser: { verb: "used the browser", group: "web" },
  // the machine
  shell: { verb: "ran", of: "command", group: "shell" },
  read_design_system: { verb: "read the design system", group: "files" },
  update_design_system: { verb: "changed the design system", group: "files" },
  find_icons: { verb: "found icons", group: "files" },
  read_file: { verb: "read", of: "paths", group: "files" },
  write_file: { verb: "wrote", of: "path", group: "files" },
  edit_file: { verb: "edited", of: "path", group: "files" },
  edit_files: { verb: "edited", group: "files" },
  changes: { verb: "checked what he had changed", group: "files" },
  commit: { verb: "saved a point in history", of: "message", group: "files" },
  check_code: { verb: "checked the code", of: "path", group: "shell" },
  run_tests: { verb: "ran the tests in", of: "path", group: "shell" },
  start_process: { verb: "started", of: "name", group: "shell" },
  check_process: { verb: "checked on", of: "name", group: "shell" },
  stop_process: { verb: "stopped", of: "name", group: "shell" },
  outline: { verb: "read the shape of", of: "paths", group: "reading" },
  repo_map: { verb: "got his bearings in", of: "paths", group: "reading" },
  find_symbol: { verb: "traced every use of", of: "name", group: "search" },
  install_language_support: { verb: "set up language support for", of: "confirm", group: "shell" },
  diagnostics: { verb: "checked for problems in", of: "path", group: "shell" },
  references: { verb: "found everywhere that uses", of: "symbol", group: "search" },
  definition: { verb: "looked up where to find", of: "symbol", group: "search" },
  rename_symbol: { verb: "renamed", of: "symbol", group: "files" },
  glob: { verb: "looked for files matching", of: "pattern", group: "search" },
  history: { verb: "looked back through his history", group: "reading" },
  delete_file: { verb: "put in the Trash", of: "path", group: "files" },
  list_files: { verb: "looked through", of: "path", group: "files" },
  grep: { verb: "searched files for", of: "patterns", group: "search" },
  // his own tools and skills
  read_skill: { verb: "opened the skill", of: "name", group: "skills" },
  plugin_state: { verb: "read what the plugin is holding", of: "plugin", group: "skills" },
  plan_work: { verb: "laid out the next tasks", group: "tasks" },
  // agents: whole conversations he starts and talks to. The subjects of the first two are inside
  // each list item (`agents[].name`, `messages[].agent`), so `describeCall` names them itself.
  start_agents: { verb: "started agents", group: "agents" },
  message_agents: { verb: "messaged", group: "agents" },
  stop_agents: { verb: "stopped", of: "agents", group: "agents" },
  close_agents: { verb: "closed", of: "agents", group: "agents" },
  wait_for_agents: { verb: "waited for his agents", group: "agents" },
};

/** What each plural argument used to be called, for reading back old tool calls. */
const SINGULAR: Record<string, string> = { paths: "path", patterns: "pattern" };

/** A tool nobody has written a phrase for: its name, made readable, never raw code. */
function fallbackVerb(name: string): string {
  return name.replace(/_/g, " ");
}

export interface DescribedCall {
  /** What he did, as a phrase: "ran", "searched the web for". */
  verb: string;
  /** The same phrase while it is still happening: "running", "searching the web for". */
  doing: string;
  /** Whose tool it was, when it is not his own: an MCP server's label or a plugin's name. */
  badge?: string;
  /** The thing he did it to, taken from the argument the phrase is about. "" when the
   *  phrase is complete on its own ("checked his reminders"). */
  subject: string;
  icon: LucideIcon;
  tone: string;
  /** Which kind of work it was, for counting a run of calls. */
  group?: keyof typeof GROUP;
}

/** Every command he runs is `cd <his folder> && ` first, so that prefix is on every single
 *  shell row and says nothing — it's not a choice he made about this call, it's where he always
 *  works. Stripped from what's shown; the real, full command a person typed is still there
 *  untouched in the expanded arguments, this only shortens the collapsed line. */
function shortenShellCommand(command: string): string {
  // `cd X && …`, `cd X; …` and `cd X` on a line of its own before the real command are all the
  // same prefix; only the first was stripped, so a multi-line script read "Ran cd ~/Desktop/…"
  // on every row and the command itself was cut off.
  return command.replace(/^\s*cd\s+\S+\s*(?:&&|;|\n)\s*/, "");
}

/** One tool call, in English: what he did, the thing he did it to, and an icon for the kind
 *  of work it was. */
/**
 * The browser's acts, in the same voice as the table above.
 *
 * The built-in `browser` tool names its act as an argument, and the Browser *plugin* it replaced
 * named each one as a tool of its own — `plugin__browser__look`. Both read from this: the first
 * is every call from now on, the second is every one already in a transcript. Without it, 560
 * browser calls in his recent transcripts each read "plugin  browser  look", which is the
 * function's name with its underscores taken out, not a sentence about what he did.
 */
const BROWSER_VERB: Record<string, { verb: string; of?: string }> = {
  open: { verb: "opened", of: "url" },
  read: { verb: "read the page" },
  click: { verb: "clicked", of: "text" },
  type: { verb: "typed into", of: "into" },
  press: { verb: "pressed", of: "key" },
  scroll: { verb: "scrolled", of: "by" },
  look: { verb: "looked at the page" },
  resize: { verb: "resized the page to", of: "preset" },
  show: { verb: "opened the Browser tab" },
  back: { verb: "went back" },
  forward: { verb: "went forward" },
  reload: { verb: "reloaded the page" },
  wait: { verb: "waited for", of: "text" },
  errors: { verb: "checked the page for errors" },
  save_test: { verb: "saved a test to", of: "path" },
  tabs: { verb: "looked at the tabs" },
  switch: { verb: "switched to tab", of: "tab" },
  close: { verb: "closed a tab" },
};

const PLUGIN_VERB: Record<string, Record<string, { verb: string; of?: string }>> = {
  browser: BROWSER_VERB,
};

/** Past tense to present, by first word, for a row that is still running. English does not
 *  derive these, so the irregular ones are written out; anything missing keeps its past form,
 *  which reads fine under a spinner. */
const PRESENT: Record<string, string> = {
  ran: "running", read: "reading", wrote: "writing", edited: "editing", searched: "searching",
  opened: "opening", started: "starting", checked: "checking", stopped: "stopping",
  traced: "tracing", looked: "looking", got: "getting", found: "finding", renamed: "renaming",
  put: "putting", saved: "saving", exchanged: "exchanging", set: "setting", updated: "updating",
  worked: "working", handed: "handing over", laid: "laying", remembered: "remembering",
  let: "letting", cleared: "clearing", reached: "reaching", took: "taking", changed: "changing",
  added: "adding", ticked: "ticking", linked: "linking", unlinked: "unlinking", clicked: "clicking",
  typed: "typing", pressed: "pressing", scrolled: "scrolling", resized: "resizing",
  "re-read": "re-reading", "re-shelved": "re-shelving", went: "going", reloaded: "reloading",
  switched: "switching", closed: "closing", waited: "waiting", is: "", "set up": "setting up",
  messaged: "messaging",
};

function presentOf(verb: string): string {
  const [first = "", ...rest] = verb.split(" ");
  const now = PRESENT[first];
  if (now === undefined) return verb;
  return [now, ...rest].filter(Boolean).join(" ");
}

/** The arguments that are paths, whose values read better relative to the project. */
const PATH_ARGS = new Set(["path", "paths", "folder", "directory"]);

/** A path as a person reads it: relative inside the project, `~` for home. */
export function shortPath(path: string, root?: string | null): string {
  const clean = path.trim();
  if (root) {
    const base = root.replace(/\/+$/, "");
    if (clean === base) return ".";
    if (clean.startsWith(`${base}/`)) return clean.slice(base.length + 1);
  }
  return clean.replace(/^\/Users\/[^/]+(?=\/|$)/, "~");
}

/** `github` → `GitHub` is beyond a table; `github` → `Github` is at least a name. */
const labelled = (id: string) => id.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

function subjectOf(raw: unknown, root?: string | null, paths = false): string {
  const one = (value: unknown) => (paths ? shortPath(String(value), root) : String(value));
  // A list reads as a list, not as `["a.py","b.py"]`. Three is the point at which naming them
  // all stops being an aid and starts being the reason the row wraps.
  if (Array.isArray(raw)) {
    return raw.length > 3
      ? `${raw.slice(0, 3).map(one).join(", ")} +${raw.length - 3} more`
      : raw.map(one).join(", ");
  }
  return raw === undefined || raw === null ? "" : one(raw);
}

export function describeCall(
  name: string,
  args?: Record<string, unknown>,
  /** The project's folder, so paths inside it read as relative. */
  root?: string | null,
): DescribedCall {
  // The Browser tab: its words come from the act it was asked for.
  if (name === "browser") {
    const known = BROWSER_VERB[String(args?.act ?? "")];
    const verb = known?.verb ?? "used the browser";
    return {
      verb,
      doing: presentOf(verb),
      subject: subjectOf(known?.of ? args?.[known.of] : undefined),
      icon: GROUP.web.icon,
      tone: GROUP.web.tone,
      group: "web",
    };
  }
  // Someone else's tool: a plugin command or an MCP server's.
  const plugin = /^plugin__(.+?)__(.+)$/.exec(name);
  if (plugin) {
    const [, id, command] = plugin as unknown as [string, string, string];
    const known = PLUGIN_VERB[id]?.[command];
    const verb = known?.verb ?? command.replace(/_/g, " ");
    const firstString = Object.values(args ?? {}).find((one) => typeof one === "string");
    const subject = known ? subjectOf(known.of ? args?.[known.of] : undefined) : subjectOf(firstString);
    const kind = id === "browser" ? GROUP.web : GROUP.skills;
    return {
      verb,
      doing: presentOf(verb),
      subject,
      icon: kind.icon,
      tone: kind.tone,
      ...(id === "browser" ? { group: "web" as const } : { badge: labelled(id), group: "skills" as const }),
    };
  }
  const mcp = /^mcp__(.+?)__(.+)$/.exec(name);
  if (mcp) {
    const [, server, tool] = mcp as unknown as [string, string, string];
    const verb = tool.replace(/_/g, " ");
    const firstString = Object.values(args ?? {}).find((one) => typeof one === "string");
    return {
      verb,
      doing: verb,
      subject: subjectOf(firstString),
      badge: labelled(server),
      icon: GROUP.tools.icon,
      tone: GROUP.tools.tone,
      group: "tools",
    };
  }

  const entry = TOOL[name];
  const group = entry ? GROUP[entry.group] : undefined;
  // Fall back to the singular the plural tools used to take. Old calls are replayed out of
  // the transcript on every resumed turn, so a week-old conversation must not lose the one
  // piece of information on its rows — `read a.py` degrading to a bare `read`.
  const raw = entry?.of ? (args?.[entry.of] ?? args?.[SINGULAR[entry.of] ?? ""]) : undefined;
  let subject = subjectOf(raw, root, Boolean(entry?.of && PATH_ARGS.has(entry.of)));
  if (name === "shell" && subject) subject = shortenShellCommand(subject);
  // `edit_files` names no file in its own arguments — they are inside each edit — so without
  // this every edit read as a bare "edited".
  if (name === "edit_files" && !subject && Array.isArray(args?.edits)) {
    const files = [
      ...new Set(
        (args.edits as Record<string, unknown>[])
          .map((edit) => edit?.path ?? args?.path)
          .filter((path): path is string => typeof path === "string"),
      ),
    ];
    subject = subjectOf(files, root, true);
  }
  // The agent tools take a list of objects, and the names are a field inside each — so the row
  // reads "started agents api, web" rather than nothing. `["all"]` is the tools' word for every
  // open agent, which reads as a name if left alone.
  if (name === "start_agents" || name === "message_agents") {
    const list = (name === "start_agents" ? args?.agents : args?.messages) as unknown;
    const field = name === "start_agents" ? "name" : "agent";
    if (Array.isArray(list)) {
      const names = list
        .map((one) => (one && typeof one === "object" ? (one as Record<string, unknown>)[field] : undefined))
        .filter((one): one is string => typeof one === "string" && one.trim() !== "");
      subject = subjectOf([...new Set(names)]);
    }
  }
  if (
    (name === "stop_agents" || name === "close_agents") &&
    Array.isArray(args?.agents) &&
    args.agents.some((one) => String(one).toLowerCase() === "all")
  ) {
    subject = "every agent";
  }
  const verb = entry?.verb ?? fallbackVerb(name);
  return {
    verb,
    doing: presentOf(verb),
    // Coerced rather than asserted: `view_task` is about an id, and a number rendered as a
    // React child is fine but a number that has been typed as a string is not.
    subject,
    icon: group?.icon ?? Wrench,
    tone: group?.tone ?? "text-muted-foreground/60",
    ...(entry ? { group: entry.group } : {}),
  };
}

/** The whole phrase for one call, for a tooltip or a title attribute. */
export function callText(call: DescribedCall): string {
  const said = [call.verb, call.subject].filter(Boolean).join(" ");
  return said.charAt(0).toUpperCase() + said.slice(1);
}

/** Longest a one-line summary may be before it stops being one. Measured against the strip at
 *  a comfortable pane width, where the label has roughly this much room before the elapsed time
 *  and the chevron. */
const SUMMARY_MAX = 72;

/**
 * One call's phrase, cut down to something that can sit on a single row.
 *
 * A shell call's subject is the command a person typed, which is routinely a heredoc, a `for`
 * loop, or four chained `curl`s over a dozen lines. Whole, it is the right thing to show in the
 * expanded row and the wrong thing to put in a heading — one such label came to about a thousand
 * characters, which is not a summary of the work, it *is* the work.
 *
 * So: the first line only, whitespace collapsed, and an ellipsis past the limit. Returns "" when
 * what is left would be too short to mean anything, which is the caller's signal to fall back to
 * counting ("2 commands") rather than to show a stub.
 */
export function summaryLine(text: string): string {
  /* The trailing `\\` is a line continuation, so keeping the first line alone leaves it pointing
   * at nothing — "ran ./domaincheck.sh \\" reads as a rendering fault rather than an elision. */
  const first = text
    .split("\n", 1)[0]!
    .replace(/\s+/g, " ")
    .replace(/\s*\\$/, "")
    .trim();
  if (first.length < 3) return "";
  if (first.length <= SUMMARY_MAX) return first;
  // Cut at a word boundary where there is one near the end, so the ellipsis does not land
  // mid-token on a path or a flag.
  const cut = first.slice(0, SUMMARY_MAX);
  const space = cut.lastIndexOf(" ");
  return `${(space > SUMMARY_MAX - 16 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

export interface SummarisedRun {
  /** One icon per kind of work in the run, in the order the kinds first appeared. */
  icons: { icon: LucideIcon; tone: string }[];
  /** "3 files · 2 commands" — what he touched, rather than how many times he called out. */
  text: string;
}

/**
 * A run of tool calls, summarised for its collapsed heading.
 *
 * "8 tool calls" is a number about the machine. "3 files · 2 commands" is a sentence about the
 * work, and it is the difference between a heading you can skim past and one you have to open
 * to learn anything at all.
 */
export function summariseRun(names: string[]): SummarisedRun {
  const counts = new Map<string, number>();
  for (const name of names) {
    const key = describeCall(name).group ?? "tools";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return {
    icons: [...counts.keys()].map((key) => ({
      icon: GROUP[key]?.icon ?? Wrench,
      tone: GROUP[key]?.tone ?? "text-muted-foreground/60",
    })),
    text: [...counts.entries()]
      .map(([key, count]) => {
        const kind = GROUP[key];
        return `${count} ${count === 1 ? (kind?.one ?? "call") : (kind?.noun ?? "calls")}`;
      })
      .join(" · "),
  };
}
