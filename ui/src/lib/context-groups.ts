/**
 * The five things a context window is made of, and the colour that says which.
 *
 * Shared by the meter in the Work rail and the detail screen it opens. One copy because they
 * draw the same bar: if the rail called tool schemas green and the screen called them orange,
 * the screen would be answering a question about a different conversation.
 *
 * **Grouped rather than eleven hues, for two reasons.** A categorical palette runs out at eight,
 * and a ninth hue is never generated — it folds. And the groups are the actual decisions: tool
 * schemas are shrunk by removing tools, the conversation by folding it, what he has read by not
 * re-reading it. Eleven colours would name eleven rows and answer none of that.
 *
 * **The hue belongs to the group, never to its size.** These were assigned by rank at first,
 * which meant the bar repainted itself whenever two categories swapped places — the one thing a
 * categorical scale must never do, since it makes the colour mean "currently third largest"
 * instead of "the conversation".
 *
 * Slots 1-5 of a palette validated against Kith's own surfaces in both modes: worst adjacent CVD
 * ΔE 9.1 light / 8.4 dark, worst adjacent normal-vision ΔE 19.6 / 19.3. Light mode warns on
 * contrast against the paper surface, which is why every reading of this bar is obliged to carry
 * labels rather than lean on the colour alone.
 */

export interface ContextGroup {
  key: string;
  label: string;
  /** What shrinking this one actually means, for the screen with room to say it. */
  hint: string;
  swatch: string;
}

export const GROUPS: readonly ContextGroup[] = [
  {
    key: "talk",
    label: "Conversation",
    hint: "what was said, plus the summary the older turns were folded into",
    swatch: "bg-[#2a78d6] dark:bg-[#3987e5]",
  },
  {
    key: "read",
    label: "What he has read",
    hint: "files, searches and skills — shrunk by not re-reading them",
    swatch: "bg-[#eb6834] dark:bg-[#d95926]",
  },
  {
    key: "tools",
    label: "Tool schemas",
    hint: "the tool block, on every request — shrunk by turning tools off",
    swatch: "bg-[#1baf7a] dark:bg-[#199e70]",
  },
  {
    key: "place",
    label: "Where he is",
    hint: "rewritten every turn, so everything ahead of it stays cached",
    swatch: "bg-[#eda100] dark:bg-[#c98500]",
  },
  {
    key: "self",
    label: "Who he is",
    hint: "your persona and standing directive — identical every turn, so nearly free",
    swatch: "bg-[#e87ba4] dark:bg-[#d55181]",
  },
] as const;

/**
 * Which group each of the ledger's categories belongs to.
 *
 * A key missing from this map is silently dropped by `byGroup` — it belongs to no group, so it
 * is counted in none of the five and the bar quietly stops summing to the total it is drawn
 * against. `directives` was in exactly that state: the ledger has given turn directives their
 * own line since they stopped arriving as fake user messages, and nothing here ever claimed
 * them, so a turn the harness nudged four times drew a bar that was short by four nudges.
 */
export const GROUP_OF: Record<string, string> = {
  messages: "talk",
  // The folded brief — older turns compressed into a summary. It rides as a `system` message on
  // the wire, but it is the conversation, so it is counted with the conversation and not under
  // "Who he is". See `repositories`/`ledger`: a summary of the chat filed under the persona was
  // the bug this fixes.
  summary: "talk",
  tool_results: "read",
  code: "read",
  skills: "read",
  images: "read",
  built_in_tools: "tools",
  mcp_tools: "tools",
  // "Where he is" in the widest sense: everything rewritten on every turn, which is what the
  // group's own hint says. The project region and the harness's mid-turn nudges are both that.
  live: "place",
  project: "place",
  directives: "place",
  persona: "self",
  system: "self",
};

/** The threshold the loop folds at, from `agent_loop`. Named so a reading can say what is about
 *  to happen rather than only how full it is — 78% and 82% look alike and are not. */
export const FOLDS_AT = 0.8;

export interface GroupReading extends ContextGroup {
  tokens: number;
  /** Share of what is *used*, not of the window. Drawn against the window the bar is honest and
   *  useless: at 5% of a million tokens all five categories are crushed into a fiftieth of the
   *  width, so the composition — the only thing the colours are for — is unreadable at exactly
   *  the usage level a conversation spends most of its life at. */
  part: number;
}

/** Roll the ledger's lines up into the five groups, in fixed order, dropping the empty ones. */
export function byGroup(
  lines: readonly { key: string; tokens: number }[],
  used: number,
): GroupReading[] {
  return GROUPS.map((group) => {
    const tokens = lines
      .filter((line) => GROUP_OF[line.key] === group.key)
      .reduce((sum, line) => sum + line.tokens, 0);
    return { ...group, tokens, part: used ? tokens / used : 0 };
  }).filter((group) => group.tokens > 0);
}
