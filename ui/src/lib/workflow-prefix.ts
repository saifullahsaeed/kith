/**
 * The `/workflow` prefix, read the same way the server reads it (`domain/workflows.py`).
 *
 * The prefix is the whole opt-in: a message that starts with it lets him write and run a workflow
 * for that request. It stays in the text on purpose — the transcript keeps it and he reads it — so
 * the bubble only has to *draw* it as what it is rather than as a word you typed.
 */
const PREFIX = "/workflow";

export function splitWorkflowPrefix(text: string): { workflow: boolean; rest: string } {
  const trimmed = text.replace(/^\s+/, "");
  if (!trimmed.toLowerCase().startsWith(PREFIX)) return { workflow: false, rest: text };
  const after = trimmed.slice(PREFIX.length);
  // `/workflows` or `/workflowish` is not the prefix; `/workflow` alone or before a space is.
  if (after && !/^\s/.test(after)) return { workflow: false, rest: text };
  return { workflow: true, rest: after.replace(/^\s+/, "") };
}
