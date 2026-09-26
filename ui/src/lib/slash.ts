/**
 * The `/` commands, as text.
 *
 * A command is a word at the start of a message, and nothing else: typed or picked from the
 * sheet, it sits in the box as `/name`, drawn as a token so you can see it was recognised, and
 * it means something when the message is sent. The sheet only finishes the word for you.
 *
 * It used to be the library's trigger popover, which *ran* a command when you picked it — and
 * running meant rewriting the whole box behind the editor's back (`/design` replaced what you had
 * typed with a paragraph of instructions), with a caret the popover never heard about. So it
 * reopened after every pick, reopened after Escape on the next keystroke, ate the space after
 * `/workflow` (which un-made the prefix), took Tab to mean "run the highlighted command", and
 * opened an empty sheet over `look at /usr/local`. Everything here is a pure function of the
 * first line and the caret, so none of that has anywhere to live.
 *
 * Two kinds:
 * - `action` commands (`/fold`, `/stop`) are the whole message and do something now.
 * - `prefix` commands (`/workflow`, `/design`, a skill) start a request; the rest of the
 *   message is the request. `/design` and skills are spelled out for him when the message is
 *   sent (`expandCommand`); `/workflow` travels as it is, because the server reads it.
 */

import { DESIGN_PROMPT } from "@/lib/commands";
import { splitWorkflowPrefix } from "@/lib/workflow-prefix";

export type SlashKind = "action" | "prefix";

export interface SlashCommand {
  name: string;
  description: string;
  kind: SlashKind;
  /** What an `action` does. Prefix commands have nothing to run; their meaning is the text. */
  run?: () => void;
}

/** The slash word the caret is in, without its `/` — or null when there is no sheet to show.
 *
 *  Only at the very start of the message. A command is always the first word (the server reads
 *  `/workflow` there and nowhere else), and anywhere else a `/` is a path or an "and/or". */
export function slashWord(firstLine: string, caret: number | null): string | null {
  if (caret === null || !firstLine.startsWith("/")) return null;
  const token = firstLine.match(/^\/\S*/)?.[0] ?? "/";
  if (caret < 1 || caret > token.length) return null;
  return token.slice(1);
}

/** The commands a word could be finishing, names that start with it first, then names that
 *  contain it. Descriptions are not searched: "wor" listed `/docx` and `/pptx` because their
 *  descriptions said "work". */
export function matchCommands(commands: readonly SlashCommand[], word: string): SlashCommand[] {
  const query = word.toLowerCase();
  const starts = commands.filter((one) => one.name.toLowerCase().startsWith(query));
  const contains = commands.filter(
    (one) => !one.name.toLowerCase().startsWith(query) && one.name.toLowerCase().includes(query),
  );
  return [...starts, ...contains];
}

/** The first line with its slash word replaced by a command. Prefix commands bring the space the
 *  request is written after; an action is the whole message and takes none. */
export function completeWord(
  firstLine: string,
  command: SlashCommand,
): { text: string; replaced: number; caret: number } {
  const token = firstLine.match(/^\/\S*/)?.[0] ?? "/";
  const after = firstLine.slice(token.length);
  const space = command.kind === "prefix" && !/^\s/.test(after) ? " " : "";
  const text = `/${command.name}${space}`;
  // The caret goes past a space that was already there, so you are straight into the request.
  const caret = text.length + (command.kind === "prefix" && space === "" ? 1 : 0);
  return { text, replaced: token.length, caret };
}

/** The command a message opens with, when it opens with one this window knows. */
export function leadingCommand(
  text: string,
  commands: readonly SlashCommand[],
): { command: SlashCommand; rest: string } | null {
  const found = text.match(/^\/(\S+)(?:\s+|$)/);
  if (!found) return null;
  const command = commands.find((one) => one.name === found[1]);
  return command ? { command, rest: text.slice(found[0].length) } : null;
}

/* ── What a sent command means ────────────────────────────────────────────────────────── */

/** The skills known to exist, for the send path, which has no hook to ask. Set when the composer
 *  reads the list; a skill it has not heard of yet is left as the word, which he can still read. */
let knownSkills = new Set<string>();

export function rememberSkills(names: readonly string[]): void {
  knownSkills = new Set(names);
}

const SKILL_PROMPT = (name: string) => `Read the ${name} skill and use it for this: `;
const SKILL_SPELLED = /^Read the (\S+) skill and use it for this: /;

/** A message as he should read it. `/design make a login` and `/some-skill do X` were spelled out
 *  in the box before; now the box keeps the word and this spells it out on the way to him. */
export function expandCommand(text: string): string {
  const found = text.match(/^\/(\S+)(?:\s+|$)/);
  if (!found) return text;
  const name = found[1];
  const rest = text.slice(found[0].length);
  if (name === "design") return DESIGN_PROMPT + rest;
  if (knownSkills.has(name)) return SKILL_PROMPT(name) + rest;
  return text;
}

/** The token a sent message opened with, so its bubble can draw it the way the box did — from the
 *  word as typed, or from the spelled-out text a transcript read back from the server holds. */
export function commandChip(
  text: string,
): { label: string; tone: "workflow" | "command"; rest: string } | null {
  const workflow = splitWorkflowPrefix(text);
  if (workflow.workflow) return { label: "/workflow", tone: "workflow", rest: workflow.rest };
  if (text.startsWith(DESIGN_PROMPT)) {
    return { label: "/design", tone: "command", rest: text.slice(DESIGN_PROMPT.length) };
  }
  const spelled = text.match(SKILL_SPELLED);
  if (spelled) return { label: `/${spelled[1]}`, tone: "command", rest: text.slice(spelled[0].length) };
  const typed = text.match(/^\/(\S+)(?:\s+|$)/);
  if (typed && (typed[1] === "design" || knownSkills.has(typed[1]))) {
    return { label: `/${typed[1]}`, tone: "command", rest: text.slice(typed[0].length) };
  }
  return null;
}
