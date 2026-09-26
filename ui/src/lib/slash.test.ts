/**
 * The `/` sheet and what a command means, as pure functions of the first line and the caret.
 *
 * Every bug the library popover had was a disagreement between the box and a copy of its state
 * somewhere else — the reopen after a pick, the reopen after Escape, the eaten space after
 * `/workflow`. These pin the rules that leave nothing to disagree about.
 */
import { afterEach, describe, expect, it } from "vitest";

import { DESIGN_PROMPT } from "@/lib/commands";
import {
  commandChip,
  completeWord,
  expandCommand,
  leadingCommand,
  matchCommands,
  rememberSkills,
  slashWord,
  type SlashCommand,
} from "@/lib/slash";

const COMMANDS: SlashCommand[] = [
  { name: "fold", kind: "action", description: "Summarise the older turns" },
  { name: "stop", kind: "action", description: "Stop the turn" },
  { name: "design", kind: "prefix", description: "Mock up screens" },
  { name: "workflow", kind: "prefix", description: "Split into agents" },
  { name: "docx", kind: "prefix", description: "Work with Word documents" },
  { name: "code-refactor", kind: "prefix", description: "Refactor code" },
];
const named = (name: string) => COMMANDS.find((one) => one.name === name)!;

afterEach(() => rememberSkills([]));

describe("when the sheet is open", () => {
  it("opens on a slash at the start of the message, with the caret in the word", () => {
    expect(slashWord("/", 1)).toBe("");
    expect(slashWord("/wor", 4)).toBe("wor");
    expect(slashWord("/wor", 2)).toBe("wor");
  });

  it("is closed once the word is finished and the caret has moved past it", () => {
    expect(slashWord("/workflow ", 10)).toBeNull();
    expect(slashWord("/workflow build it", 18)).toBeNull();
  });

  it("is closed for a slash anywhere but the start — a path, an and/or", () => {
    expect(slashWord("look at /usr/local", 18)).toBeNull();
    expect(slashWord("and/or", 6)).toBeNull();
  });

  it("is closed with the caret before the slash, or no caret in the line", () => {
    expect(slashWord("/fold", 0)).toBeNull();
    expect(slashWord("/fold", null)).toBeNull();
  });
});

describe("what it offers", () => {
  it("puts names that start with the word first, then names that contain it", () => {
    expect(matchCommands(COMMANDS, "o").map((one) => one.name)).toEqual(["fold", "stop", "workflow", "docx", "code-refactor"]);
    expect(matchCommands(COMMANDS, "d").map((one) => one.name)).toEqual(["design", "docx", "fold", "code-refactor"]);
  });

  it("does not search descriptions — 'wor' is /workflow, not every skill that says 'Work'", () => {
    expect(matchCommands(COMMANDS, "wor").map((one) => one.name)).toEqual(["workflow"]);
  });

  it("offers everything for a bare slash, and nothing for a word nothing matches", () => {
    expect(matchCommands(COMMANDS, "")).toHaveLength(COMMANDS.length);
    expect(matchCommands(COMMANDS, "zzz")).toEqual([]);
  });
});

describe("taking a command", () => {
  it("finishes a prefix with the space its request goes after, caret past it", () => {
    expect(completeWord("/wor", named("workflow"))).toEqual({ text: "/workflow ", replaced: 4, caret: 10 });
  });

  it("keeps what was already written after the word, and does not double its space", () => {
    const done = completeWord("/wor build the devices screen", named("workflow"));
    expect(done).toEqual({ text: "/workflow", replaced: 4, caret: 10 });
    const line = done.text + "/wor build the devices screen".slice(done.replaced);
    expect(line).toBe("/workflow build the devices screen");
  });

  it("leaves an action as the whole word, with no space", () => {
    expect(completeWord("/fo", named("fold"))).toEqual({ text: "/fold", replaced: 3, caret: 5 });
  });
});

describe("a command at the start of a message", () => {
  it("is found only as a whole word", () => {
    expect(leadingCommand("/fold", COMMANDS)?.command.name).toBe("fold");
    expect(leadingCommand("/workflow build it", COMMANDS)?.rest).toBe("build it");
    expect(leadingCommand("/folder", COMMANDS)).toBeNull();
    expect(leadingCommand("please /fold", COMMANDS)).toBeNull();
  });
});

describe("what he is sent", () => {
  it("spells /design out as the design request", () => {
    expect(expandCommand("/design a login screen")).toBe(DESIGN_PROMPT + "a login screen");
  });

  it("spells a known skill out as 'read it and use it'", () => {
    rememberSkills(["code-refactor"]);
    expect(expandCommand("/code-refactor the adapter")).toBe(
      "Read the code-refactor skill and use it for this: the adapter",
    );
  });

  it("leaves /workflow, unknown words and ordinary text exactly as typed", () => {
    expect(expandCommand("/workflow build it")).toBe("/workflow build it");
    expect(expandCommand("/nope hi")).toBe("/nope hi");
    expect(expandCommand("hello /design")).toBe("hello /design");
  });
});

describe("the chip on a sent message", () => {
  it("reads the word as typed and the spelled-out text a reload brings back as the same chip", () => {
    rememberSkills(["code-refactor"]);
    expect(commandChip("/code-refactor the adapter")).toEqual({ label: "/code-refactor", tone: "command", rest: "the adapter" });
    expect(commandChip(expandCommand("/code-refactor the adapter"))).toEqual({
      label: "/code-refactor",
      tone: "command",
      rest: "the adapter",
    });
    expect(commandChip(DESIGN_PROMPT + "a login")).toEqual({ label: "/design", tone: "command", rest: "a login" });
  });

  it("keeps /workflow its own tone, and leaves ordinary text alone", () => {
    expect(commandChip("/workflow build it")).toEqual({ label: "/workflow", tone: "workflow", rest: "build it" });
    expect(commandChip("/usr/local is full")).toBeNull();
    expect(commandChip("hello")).toBeNull();
  });
});
