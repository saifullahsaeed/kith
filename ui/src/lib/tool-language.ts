/**
 * What each of Kith's tools did, in English, with an icon for the kind of work it was.
 *
 * This lived inside mind-panel.tsx, which meant the Mind feed said "read ~/Kith/cv.pdf" while
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
  // memory and notes
  recall: { verb: "searched his memory for", of: "query", group: "memory" },
  remember: { verb: "remembered", of: "content", group: "memory" },
  forget: { verb: "let go of a memory", group: "memory" },
  set_memory_level: { verb: "re-shelved a memory", group: "memory" },
  take_note: { verb: "noted", of: "title", group: "writing" },
  read_notes: { verb: "read his notes", group: "reading" },
  update_note: { verb: "edited a note", group: "writing" },
  journal: { verb: "wrote in his journal", group: "writing" },
  read_journal: { verb: "re-read his journal", group: "reading" },
  // tasks and projects
  add_task: { verb: "set himself", of: "goal", group: "tasks" },
  list_tasks: { verb: "looked over his tasks", group: "tasks" },
  update_task: { verb: "updated a task", group: "tasks" },
  view_task: { verb: "opened task", of: "id", group: "tasks" },
  comment_on_task: { verb: "noted on a task", of: "comment", group: "writing" },
  ask_on_task: { verb: "asked you", of: "question", group: "outreach" },
  add_checklist_item: { verb: "added a step", of: "text", group: "tasks" },
  check_item: { verb: "ticked off a step", group: "tasks" },
  add_deliverable: { verb: "handed over", of: "title", group: "tasks" },
  create_project: { verb: "started the project", of: "name", group: "projects" },
  list_projects: { verb: "looked over his projects", group: "projects" },
  update_project: { verb: "updated a project", group: "projects" },
  add_milestone: { verb: "added the milestone", of: "title", group: "projects" },
  update_milestone: { verb: "updated a milestone", group: "projects" },
  order_milestones: { verb: "put the milestones in order", group: "projects" },
  unlink_milestones: { verb: "unlinked two milestones", group: "projects" },
  link_folder: { verb: "linked a folder", of: "folder", group: "projects" },
  // mood, identity, people
  set_mood: { verb: "felt", of: "mood", group: "self" },
  set_identity: { verb: "reshaped who he is", group: "self" },
  note_about_self: { verb: "noted about himself", of: "note", group: "self" },
  note_about: { verb: "noted about you", of: "note", group: "self" },
  recall_person: { verb: "recalled who you are", group: "self" },
  // time
  set_reminder: { verb: "set a reminder", of: "note", group: "time" },
  list_reminders: { verb: "checked his reminders", group: "time" },
  cancel_reminder: { verb: "cleared a reminder", group: "time" },
  schedule: { verb: "set a standing job", of: "note", group: "time" },
  list_schedules: { verb: "checked his schedules", group: "time" },
  cancel_schedule: { verb: "cleared a schedule", group: "time" },
  // reaching out
  reach_out: { verb: "reached out to you", group: "outreach" },
  // knowledge and the web
  search_sources: { verb: "searched your sources for", of: "query", group: "search" },
  read_source: { verb: "read a source you gave him", group: "reading" },
  web_search: { verb: "searched the web for", of: "query", group: "search" },
  fetch_url: { verb: "read", of: "url", group: "web" },
  browse_page: { verb: "opened", of: "url", group: "web" },
  // the machine
  shell: { verb: "ran", of: "command", group: "shell" },
  read_file: { verb: "read", of: "path", group: "files" },
  write_file: { verb: "wrote", of: "path", group: "files" },
  edit_file: { verb: "edited", of: "path", group: "files" },
  edit_files: { verb: "edited several files at once", group: "files" },
  changes: { verb: "checked what he had changed", group: "files" },
  commit: { verb: "saved a point in history", of: "message", group: "files" },
  check_code: { verb: "checked the code", of: "path", group: "shell" },
  run_tests: { verb: "ran the tests in", of: "path", group: "shell" },
  start_process: { verb: "started", of: "name", group: "shell" },
  check_process: { verb: "checked on", of: "name", group: "shell" },
  stop_process: { verb: "stopped", of: "name", group: "shell" },
  outline: { verb: "read the shape of", of: "path", group: "reading" },
  repo_map: { verb: "got his bearings in", of: "path", group: "reading" },
  diagnostics: { verb: "checked for problems in", of: "path", group: "shell" },
  references: { verb: "found everywhere that uses", of: "symbol", group: "search" },
  definition: { verb: "looked up where to find", of: "symbol", group: "search" },
  rename_symbol: { verb: "renamed", of: "symbol", group: "files" },
  glob: { verb: "looked for files matching", of: "pattern", group: "search" },
  history: { verb: "looked back through his history", group: "reading" },
  delete_file: { verb: "put in the Trash", of: "path", group: "files" },
  list_files: { verb: "looked through", of: "path", group: "files" },
  grep: { verb: "searched files for", of: "pattern", group: "search" },
  // his own tools and skills
  create_tool: { verb: "built himself a tool", of: "name", group: "tools" },
  list_tools: { verb: "checked his own tools", group: "tools" },
  delete_tool: { verb: "removed one of his tools", group: "tools" },
  read_skill: { verb: "opened the skill", of: "name", group: "skills" },
};

/** A tool nobody has written a phrase for: its name, made readable, never raw code. */
export function fallbackVerb(name: string): string {
  return name.replace(/_/g, " ");
}

export interface DescribedCall {
  /** What he did, as a phrase: "ran", "searched the web for". */
  verb: string;
  /** The thing he did it to, taken from the argument the phrase is about. "" when the
   *  phrase is complete on its own ("checked his reminders"). */
  subject: string;
  icon: LucideIcon;
  tone: string;
  /** Which kind of work it was, for counting a run of calls. */
  group?: keyof typeof GROUP;
}

/** One tool call, in English: what he did, the thing he did it to, and an icon for the kind
 *  of work it was. */
export function describeCall(name: string, args?: Record<string, unknown>): DescribedCall {
  const entry = TOOL[name];
  const group = entry ? GROUP[entry.group] : undefined;
  const raw = entry?.of ? args?.[entry.of] : undefined;
  return {
    verb: entry?.verb ?? fallbackVerb(name),
    // Coerced rather than asserted: `view_task` is about an id, and a number rendered as a
    // React child is fine but a number that has been typed as a string is not.
    subject: raw === undefined || raw === null ? "" : String(raw),
    icon: group?.icon ?? Wrench,
    tone: group?.tone ?? "text-muted-foreground/60",
    ...(entry ? { group: entry.group } : {}),
  };
}

/** The whole phrase for one call, for a tooltip or a title attribute. */
export function callText(call: DescribedCall): string {
  return [call.verb, call.subject].filter(Boolean).join(" ");
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
    const key = TOOL[name]?.group ?? "tools";
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
