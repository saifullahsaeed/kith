/** Client for the persona folder — his fragments, editable. */

export interface PersonaFragment {
  /** Path relative to the persona folder, which is also its identity and its order. */
  name: string;
  /** "40-how-you-work.md" → "how you work". The numeric prefix is ordering, not a name. */
  title: string;
  enabled: boolean;
  /** The file, all of it. What a text editor would show you. */
  chars: number;
  /**
   * The part of the file that reaches the model — comments stripped, edges trimmed.
   *
   * The one that answers "what does this cost me", and the one that was missing. `chars` was
   * being shown under a heading about what every request carries, and on this repo's own persona
   * the two disagree by 31%: `35-how-you-spend-a-round.md` is 2,580 bytes of which 910 are his.
   * The rest is the note explaining why the fragment exists, which he never sees.
   */
  promptChars: number;
  body: string;
}

/** One fragment's contribution to the prompt, as the model receives it. */
export interface PersonaPart {
  name: string;
  text: string;
}

export interface PersonaState {
  folder: string;
  fragments: PersonaFragment[];
  /**
   * The same text as `merged`, still labelled with the file each piece came from.
   *
   * `merged` is literally `parts.map(p => p.text).join("\n\n")` on the server, so showing the
   * parts cannot show something he does not receive — which is the only reason it is safe to
   * render anything other than `merged` itself.
   */
  parts: PersonaPart[];
  /** What actually reaches the model: fragments joined, comments stripped. */
  merged: string;
  chars: number;
}

export async function fetchPersona(): Promise<PersonaState> {
  const response = await fetch("/api/persona");
  if (!response.ok) throw new Error(`/api/persona returned ${response.status}`);
  return (await response.json()) as PersonaState;
}

/**
 * Every write returns the fragment as it now is on disk, and callers get it.
 *
 * It used to return `Promise<void>` and drop the body, which cost more than a refetch. Enabling a
 * fragment is a *rename* on the server — the underscore prefix is the on-disk convention — so a
 * caller that wants to keep its selection has to know the new filename. With the response thrown
 * away the only way to know it was to re-derive the rename rule in TypeScript, and the copy was
 * wrong: it prefixed the first path segment rather than the filename, so disabling anything in a
 * subfolder predicted `_sub/10-x.md` where the server wrote `sub/_10-x.md`. The server already
 * knew the answer. Now it is allowed to say it.
 */
async function send(url: string, method: string, body?: unknown): Promise<PersonaFragment> {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const payload = (await response.json().catch(() => ({}))) as PersonaFragment & { error?: string };
  if (!response.ok) throw new Error(payload.error ?? `that didn't work (${response.status})`);
  return payload;
}

const path = (name: string) => `/api/persona/${name.split("/").map(encodeURIComponent).join("/")}`;

export const savePersonaFragment = (name: string, body: string) =>
  send(path(name), "PUT", { body });
export const addPersonaFragment = (name: string, body: string) =>
  send("/api/persona", "POST", { name, body });
export const setPersonaFragmentEnabled = (name: string, enabled: boolean) =>
  send(path(name), "PATCH", { enabled });
export const renamePersonaFragment = (name: string, next: string) =>
  send(path(name), "PATCH", { name: next });
/** The one that has nothing to return. */
export const deletePersonaFragment = async (name: string): Promise<void> => {
  await send(path(name), "DELETE");
};
