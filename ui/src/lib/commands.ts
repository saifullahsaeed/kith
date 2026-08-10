/**
 * What `/` can do.
 *
 * Everything here was already possible and none of it was reachable without asking him in
 * prose and hoping. Folding is the clearest case: a turn folds itself when the window gets
 * tight, so the one moment you want it — before sending something long into a conversation you
 * know is bloated — is the one moment it will not happen, and the fold lands in the middle of
 * the turn you were waiting on instead.
 *
 * Definitions only. The menu, the filtering and the keyboard handling are assistant-ui's
 * (`unstable_useSlashCommandAdapter`), so this is a table rather than a widget.
 */

export interface CommandResult {
  /** Said back in the composer's own status line. Empty when there is nothing to report. */
  note: string;
}

export async function foldNow(conversationId: string): Promise<CommandResult> {
  if (!conversationId) return { note: "Nothing to fold — this conversation has not started." };
  const response = await fetch(`/api/chat/${conversationId}/fold`, { method: "POST" });
  if (!response.ok) return { note: "Could not fold it." };
  const body = (await response.json()) as {
    folded?: boolean;
    note?: string;
    fromChars?: number;
    toChars?: number;
  };
  if (!body.folded) return { note: body.note ?? "Nothing to fold." };
  // Characters, not tokens: it is what the server measured, and dividing by four to sound
  // precise would be inventing a figure.
  const saved = Math.max(0, (body.fromChars ?? 0) - (body.toChars ?? 0));
  return { note: `Folded — ${saved.toLocaleString()} characters summarised.` };
}

export async function stopTurn(conversationId: string): Promise<CommandResult> {
  if (!conversationId) return { note: "" };
  const response = await fetch(`/api/chat/${conversationId}/stop`, { method: "POST" });
  const body = response.ok ? ((await response.json()) as { stopping?: boolean }) : {};
  return { note: body.stopping ? "Stopping." : "Nothing is running." };
}

export interface SkillSummary {
  name: string;
  description?: string;
}

/** His skills, for the `/skill` list. Read once when the menu opens rather than held: he can
 *  write a new one mid-conversation, and a stale list would not offer it. */
export async function listSkills(): Promise<SkillSummary[]> {
  const response = await fetch("/api/skills").catch(() => null);
  if (!response || !response.ok) return [];
  const body = (await response.json()) as { skills?: SkillSummary[] } | SkillSummary[];
  const skills = Array.isArray(body) ? body : (body.skills ?? []);
  return skills.filter((one) => one && one.name);
}
