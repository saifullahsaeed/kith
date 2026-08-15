/**
 * What can answer a question about meaning in the folder he is working in.
 *
 * The semantic tools — references, definition, rename — need a language server, and most
 * machines have none. That is ordinary rather than broken, and it is reported as status
 * with a remedy rather than as an error.
 *
 * Installing from here involves no permission prompt, and that is deliberate: the gate
 * exists to stop *Kith* changing your machine without asking, and a person clicking a
 * button in their own settings window is the asking. His own route through the chat still
 * goes through the gate, because there the actor is him.
 */

/** One language found in the folder, and what can serve it. */
export type LanguageServerState = {
  /** The tree-sitter family name — "python", "typescript" — as the outline spells it. */
  family: string;
  /** How many files of it are here. Two is the floor; one stray file is not a language. */
  files: number;
  /** Whether something can already answer semantic questions about it. */
  served: boolean;
  /** Whether Kith can fetch one. False for Go, Rust, Ruby and C, which come from their own
   *  package managers — `manual` carries the command for those. */
  installable: boolean;
  /** Roughly what lands on disk, in words. Empty when we do not install it. */
  size: string;
  /** What to run by hand. Always present, and the only option when `installable` is false. */
  manual: string;
};

export type LanguageServers = {
  /** The folder inspected. Shown because "this needs pyright" is only useful next to which
   *  project — and it follows the work rather than sitting at the workspace root. */
  root: string;
  languages: LanguageServerState[];
};

export async function fetchLanguageServers(): Promise<LanguageServers> {
  const response = await fetch("/api/language-servers");
  if (!response.ok) throw new Error(`could not read language support (${response.status})`);
  return (await response.json()) as LanguageServers;
}

/** Install the one for this language. Available on his next call, with no restart. */
export async function installLanguageServer(family: string): Promise<void> {
  const response = await fetch("/api/language-servers/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ family }),
  });
  const body = (await response.json()) as { error?: string };
  if (!response.ok) throw new Error(body.error ?? `installing failed (${response.status})`);
}
