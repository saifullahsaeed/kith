/** Client for the persona folder — his fragments, editable. */

export interface PersonaFragment {
  /** Path relative to the persona folder, which is also its identity and its order. */
  name: string;
  /** "40-how-you-work.md" → "how you work". The numeric prefix is ordering, not a name. */
  title: string;
  enabled: boolean;
  chars: number;
  body: string;
}

export interface PersonaState {
  folder: string;
  fragments: PersonaFragment[];
  /** What actually reaches the model: fragments joined, comments stripped. */
  merged: string;
  chars: number;
}

export async function fetchPersona(): Promise<PersonaState> {
  const response = await fetch("/api/persona");
  if (!response.ok) throw new Error(`/api/persona returned ${response.status}`);
  return (await response.json()) as PersonaState;
}

async function send(url: string, method: string, body?: unknown): Promise<void> {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(detail.error ?? `that didn't work (${response.status})`);
  }
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
export const deletePersonaFragment = (name: string) => send(path(name), "DELETE");
