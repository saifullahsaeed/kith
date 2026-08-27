import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, FolderOpen, Loader2, Plus, RotateCcw, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { usePrompt } from "@/components/ui/prompt";
import { useConfirm } from "@/components/ui/confirm";
import {
  addPersonaFragment,
  deletePersonaFragment,
  fetchPersona,
  renamePersonaFragment,
  savePersonaFragment,
  setPersonaFragmentEnabled,
  type PersonaFragment,
  type PersonaPart,
  type PersonaState,
} from "@/lib/backend";
import { openOnHost } from "@/lib/files";
import type { ConnectionState } from "@/lib/backend";
import { estimateTokens } from "@/lib/tokens";
import { cn } from "@/lib/utils";

/**
 * Who he is, as the files it actually is.
 *
 * The persona was a single textarea holding the merged prompt, which was wrong in a way
 * that mattered: it is six files, they are merged in filename order, and the merged text is
 * an *output*. Editing the output meant you could not add a paragraph without owning the
 * whole thing, could not turn one idea off to see what changed, and could not tell which
 * part of thirteen thousand characters you were responsible for.
 *
 * So this edits the fragments. The folder stays the source of truth — someone who prefers
 * a text editor can open it and get the same result, which is why "Reveal" is here and why
 * enabling is a rename rather than a database flag.
 *
 * Three things were still wrong with that, and they are what this pass is:
 *
 * **It was drawn as a document.** The settings shell padded it, centred it in a 768px
 * measure and owned the scrollbar, because that is right for the five tabs that are forms.
 * This one is an editor: given a 1500x1330 pane it used 770x640 of it and left the rest as
 * background, with the writing area hard-coded to `h-[30rem]` and the source's own 95-column
 * wrapping re-wrapping raggedly inside it. The fix is not a wider measure, it is that the
 * shell now has a `fill` layout and this tab takes the pane and manages its own scrolling.
 *
 * **The number under "every request carries all of this" was the wrong number.** It was file
 * size. What reaches him is the file with its `<!-- -->` notes stripped, and on this repo's
 * own persona those disagree by 31% — `35-how-you-spend-a-round.md` is 2,580 bytes of which
 * 910 are his, the rest being the note explaining why it exists. Read as a cost list it was
 * ranking fragments partly by how well-commented they were. The server now reports both, and
 * the one on screen is the one with a consequence.
 *
 * **One draft at a time meant switching fragments could destroy work,** which was handled by
 * asking. Drafts are now held per fragment and survive a switch, a close, and a reload, so
 * the question does not come up — the list marks what is unsaved and you can leave it that
 * way. Nothing is autosaved: this text goes into every request he ever makes, so it changes
 * who he is when a person says so and not before.
 */

/**
 * A fragment is two things in one file, and the editor was showing them as one.
 *
 * `<!-- … -->` is a note to yourself: the loader strips it before he ever sees it, which is why
 * the numbers in the list are 31% smaller than the files. But it was sitting at the top of the
 * writing area in the same font as the prose, so the first thing you read when you opened a
 * fragment was the paragraph he does not read — and on `35-how-you-spend-a-round.md` the note is
 * 1,670 characters against 910 of actual persona. The bigger half of the box was the half that
 * does nothing.
 *
 * So they get separate fields. The file is untouched as a format — this is a view of it, and
 * `join` puts it back exactly as it was found, including the whitespace between the two, so
 * saving from here and saving from a text editor produce the same bytes.
 *
 * Only when the shape is unambiguous. One leading comment and no others is every real fragment
 * in the folder; anything else — a note in the middle, two notes — is left as one plain box,
 * because guessing at how to reassemble interleaved text is how a paragraph goes missing.
 */
interface Note {
  /** Inside the `<!-- -->`, trimmed. Empty when the fragment has no note. */
  note: string;
  /** What he actually reads. */
  text: string;
  /** The note exactly as found, delimiters and all, re-emitted verbatim when it is unedited. */
  raw: string;
  /** Whitespace found between the note and the text, kept so a save is byte-identical. */
  gap: string;
  /** False when comments are interleaved: show one box and do not try to split it. */
  separable: boolean;
}

const LEADING_NOTE = /^(\s*)<!--([\s\S]*?)-->([\s\S]*?)(?=\S|$)/;

function readNote(body: string): Note {
  const found = LEADING_NOTE.exec(body);
  const text = found ? body.slice(found[0].length) : body;
  if (text.includes("<!--"))
    return { note: "", text: body, raw: "", gap: "", separable: false };
  return {
    note: found ? found[2].trim() : "",
    text,
    raw: found ? `${found[1]}<!--${found[2]}-->` : "",
    gap: found ? found[3] : "\n\n",
    separable: true,
  };
}

function writeNote(parts: Note, next: Partial<Note>): string {
  const { note, text, gap } = { ...parts, ...next };
  if (!parts.separable) return text;
  if (!note.trim()) return text;
  // Untouched notes go back exactly as they came. Re-wrapping an unedited note would rewrite
  // every fragment's first three lines the first time anyone fixed a typo further down, and
  // the folder is meant to be readable in a text editor by someone who did not use this screen.
  const head = note === parts.note && parts.raw ? parts.raw : formatNote(note);
  return `${head}${gap || "\n\n"}${text}`;
}

/** A note written here, laid out the way the folder's own notes are. */
function formatNote(note: string): string {
  return note.includes("\n") ? `<!--\n${note}\n-->` : `<!-- ${note} -->`;
}

/** Unsaved text, keyed by fragment. Survives closing Settings; see `DRAFTS`. */
type Drafts = Record<string, string>;

const DRAFTS = "kith-persona-drafts";

function readDrafts(): Drafts {
  try {
    const raw = localStorage.getItem(DRAFTS);
    return raw ? (JSON.parse(raw) as Drafts) : {};
  } catch {
    return {};
  }
}

export function PersonaTab({ connection }: { connection: ConnectionState }) {
  const [state, setState] = useState<PersonaState | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [drafts, setDrafts] = useState<Drafts>(readDrafts);
  const [busy, setBusy] = useState(false);
  const [showing, setShowing] = useState<"files" | "prompt">("files");
  // Shut to begin with: the note explains the fragment, the fragment is what you came to edit.
  const [noteOpen, setNoteOpen] = useState(false);
  const [error, setError] = useState("");
  const prompt = usePrompt();
  const confirm = useConfirm();

  /* Which reload is the current one.
   *
   * Every write reloads the folder, and the writes are quick enough to overlap: save
   * `10-how-you-judge.md` and click `20-how-you-answer.md` while it is in flight, and the
   * save's reload lands afterwards carrying `keep: "10-how-you-judge.md"` — pulling the
   * selection back to a fragment you had already left. Only the newest request is allowed to
   * land; the others are answers to questions nobody is asking any more. */
  const reload = useRef(0);

  const load = useCallback((keep?: string) => {
    const mine = ++reload.current;
    fetchPersona()
      .then((next) => {
        if (reload.current !== mine) return;
        setState(next);
        setSelected((current) => {
          const wanted = keep ?? current;
          return next.fragments.some((one) => one.name === wanted)
            ? wanted
            : (next.fragments[0]?.name ?? "");
        });
        // A draft for a fragment that is no longer there is dead weight in storage and a
        // phantom "unsaved" count. Renames carry their draft across in `act`, so anything
        // left unmatched here really is gone.
        setDrafts((held) => {
          const alive = Object.fromEntries(
            Object.entries(held).filter(([name]) =>
              next.fragments.some((one) => one.name === name),
            ),
          );
          return Object.keys(alive).length === Object.keys(held).length ? held : alive;
        });
      })
      .catch((err: unknown) => {
        if (reload.current !== mine) return;
        setError(err instanceof Error ? err.message : String(err));
      });
  }, []);

  useEffect(() => load(), [load]);

  useEffect(() => {
    try {
      localStorage.setItem(DRAFTS, JSON.stringify(drafts));
    } catch {
      /* a full or disabled store is not worth an error message here */
    }
  }, [drafts]);

  const current = state?.fragments.find((one) => one.name === selected) ?? null;
  const draft = current ? (drafts[current.name] ?? current.body) : "";
  const dirty = current !== null && draft !== current.body;

  // The draft stays one string — the file — so saving, reverting, the dirty dot and the stored
  // copy all keep working on the thing that is actually written. The two fields are a view of it.
  const parts = readNote(draft);
  const write = (next: Partial<Note>) => {
    if (!current) return;
    setDrafts((held) => ({ ...held, [current.name]: writeNote(parts, next) }));
  };

  /** Every fragment with unsaved text, in merge order. */
  const unsaved = useMemo(
    () => (state?.fragments ?? []).filter((one) => (drafts[one.name] ?? one.body) !== one.body),
    [state, drafts],
  );

  /**
   * Run a write, then take the server at its word about what the file is now called.
   *
   * Every one of these endpoints returns the fragment it just wrote. Enabling is a rename
   * underneath — the underscore prefix is the on-disk convention the loader reads — so the
   * new name is not derivable from the old one without re-implementing that rule, and the
   * previous version of this file did re-implement it, wrongly, for anything in a subfolder.
   * The `moved` argument is the draft's old key when the name changes, so unsaved text
   * follows its file rather than being stranded under a name nothing has any more.
   */
  const act = async (
    work: () => Promise<PersonaFragment>,
    moved?: string,
  ): Promise<PersonaFragment | null> => {
    setBusy(true);
    setError("");
    try {
      const written = await work();
      if (moved && moved !== written.name) {
        setDrafts((held) => {
          if (!(moved in held)) return held;
          const { [moved]: text, ...rest } = held;
          return { ...rest, [written.name]: text };
        });
      }
      load(written.name);
      return written;
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    } finally {
      setBusy(false);
    }
  };

  /**
   * Save one fragment and forget its draft — in that order, and only on success.
   *
   * The order is the whole point. `act` reports failure by returning null rather than throwing,
   * so a save that 404s or hits a read-only folder has to leave the text exactly where it is:
   * dropping the draft on the way out would delete the person's paragraph and show them the
   * old file, with an error message above it that reads like it is about something else.
   */
  const save = async (fragment: PersonaFragment, text: string) => {
    const written = await act(() => savePersonaFragment(fragment.name, text));
    if (!written) return;
    setDrafts((held) => {
      const { [fragment.name]: _saved, ...rest } = held;
      return rest;
    });
  };

  /** One at a time: each of these reloads the folder, and six at once is six answers racing. */
  const saveAll = async (fragments: PersonaFragment[]) => {
    for (const one of fragments) await save(one, drafts[one.name] ?? one.body);
  };

  // Cmd-S. It is a text editor; muscle memory arrives before the mouse does, and without
  // this the browser's own Save dialog appears over the top of it. A ref so the handler is
  // bound once and still sees the current fragment.
  const saveRef = useRef<(() => void) | null>(null);
  saveRef.current = current && dirty && !busy ? () => void save(current, draft) : null;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "s") return;
      // Not while a dialog is up — Rename and the delete confirmation are their own context,
      // and the editor behind them is not what ⌘S means at that moment.
      if (document.querySelector('[role="dialog"]')) return;
      event.preventDefault();
      saveRef.current?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Order matters: the error check is first because it is the state that never resolves. It used
  // to be last, below a `if (!state) return <spinner>`, so a server that was down showed
  // "Reading his persona…" forever with the reason sitting in a paragraph nothing could reach.
  if (error && !state) {
    return (
      <div className="space-y-2 p-6">
        <p className="text-destructive text-sm">{error}</p>
        <Button variant="outline" size="sm" onClick={() => (setError(""), load())}>
          Try again
        </Button>
      </div>
    );
  }

  if (!state) {
    return (
      <p className="text-muted-foreground flex items-center gap-2 p-6 text-sm">
        <Loader2 className="size-4 animate-spin" /> Reading his persona…
      </p>
    );
  }

  //: What every request carries, for the one share figure that is worth a number: how much of
  //: his prompt the fragment you are editing accounts for.
  const carried = state.fragments
    .filter((one) => one.enabled)
    .reduce((sum, one) => sum + one.promptChars, 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="border-border/60 flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b px-5 py-3">
        <h2 className="text-sm font-semibold">Who he is</h2>
        {/* Told per provider, because it was told to everyone. A local model re-reads this
            prompt in full on every request — `llm/caching.py` has no breakpoint to give it and
            no provider cache behind it — so the one sentence on this page about what the
            persona costs was promising "free after the first time" to precisely the people for
            whom it is not. */}
        <span
          className="text-muted-foreground/70 font-mono text-[11px] tabular-nums"
          title={
            connection.kind === "ollama"
              ? "Every request carries all of this, and a local model re-reads it in full every time — there is no provider cache to hold it."
              : "Every request carries all of this. It is also the part that caches, so it costs once every few minutes rather than once per message."
          }
        >
          {state.chars.toLocaleString()} chars · ~{estimateTokens(state.chars).toLocaleString()}{" "}
          tokens in every request
        </span>

        <div className="flex-1" />

        {/* Files / Prompt. The merged text has always come down with every fetch and was never
            shown, so the one question this screen could not answer was the one it is about:
            what does he actually end up reading. */}
        <div className="bg-muted/60 flex shrink-0 items-center gap-0.5 rounded-lg p-0.5">
          {(["files", "prompt"] as const).map((view) => (
            <button
              key={view}
              type="button"
              onClick={() => setShowing(view)}
              aria-pressed={showing === view}
              className={cn(
                "rounded-md px-2.5 py-1 text-[11px] transition-colors",
                showing === view
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {view === "files" ? "Files" : "What he reads"}
            </button>
          ))}
        </div>

        <Button variant="outline" size="sm" onClick={() => void openOnHost(state.folder, true)}>
          <FolderOpen className="size-3.5" />
          Reveal
        </Button>
      </header>

      {showing === "prompt" ? (
        <MergedPrompt parts={state.parts} unsaved={unsaved.length} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <aside className="border-border/60 flex w-72 shrink-0 flex-col border-e">
            <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2">
              {state.fragments.map((fragment) => (
                <FragmentRow
                  key={fragment.name}
                  fragment={fragment}
                  selected={fragment.name === selected}
                  dirty={(drafts[fragment.name] ?? fragment.body) !== fragment.body}
                  busy={busy}
                  onOpen={() => setSelected(fragment.name)}
                  onToggle={(enabled) =>
                    void act(() => setPersonaFragmentEnabled(fragment.name, enabled), fragment.name)
                  }
                />
              ))}
            </div>

            <div className="border-border/60 border-t p-2">
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                disabled={busy}
                onClick={() => {
                  void prompt({
                    title: "Name the fragment",
                    description: "The number decides where it lands in the merge.",
                    initial: "50-my-fragment.md",
                    confirmLabel: "Add",
                  }).then((name) => {
                    if (!name) return;
                    void act(() => addPersonaFragment(name, ""));
                  });
                }}
              >
                <Plus className="size-3.5" />
                Add a fragment
              </Button>
            </div>
          </aside>

          {current ? (
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="border-border/60 flex shrink-0 flex-wrap items-center gap-2 border-b px-4 py-2">
                <code className="text-muted-foreground min-w-0 flex-1 truncate font-mono text-[11px]">
                  {current.name}
                </code>
                <span
                  className="text-muted-foreground/60 shrink-0 font-mono text-[10px] tabular-nums"
                  title={`${current.chars.toLocaleString()} characters in the file; the rest is notes in <!-- -->, stripped before he reads it`}
                >
                  {current.promptChars.toLocaleString()} chars
                  {carried > 0 && current.enabled
                    ? ` · ${Math.round((current.promptChars / carried) * 100)}% of his prompt`
                    : ""}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    void prompt({
                      title: "Rename this fragment",
                      description:
                        "The number prefix is the merge order, so this is also how you move it.",
                      initial: current.name,
                    }).then((next) => {
                      if (next && next !== current.name) {
                        void act(() => renamePersonaFragment(current.name, next), current.name);
                      }
                    });
                  }}
                >
                  Rename
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive"
                  disabled={busy}
                  aria-label={`Delete ${current.name}`}
                  onClick={async () => {
                    const ok = await confirm({
                      title: "Delete this fragment?",
                      subject: current.name,
                      description: "It is removed from the folder. There is no undo.",
                      destructive: true,
                    });
                    if (ok) void act(() => deletePersonaFragment(current.name).then(() => current));
                  }}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>

              {parts.separable ? (
                <NoteField
                  note={parts.note}
                  open={noteOpen}
                  onOpen={setNoteOpen}
                  onChange={(note) => write({ note })}
                />
              ) : null}

              {/* Prose, not code: this is writing, and a 12px monospace box discourages
                  writing in it. Spellcheck on for the same reason — his persona is English.
                  It fills what is left of the pane rather than declaring a height, which is
                  the whole reason the tab asks the shell for `fill`. */}
              <textarea
                value={parts.text}
                spellCheck
                aria-label={`${current.title} — what he reads`}
                onChange={(event) => write({ text: event.target.value })}
                placeholder="Write in the second person — you are telling him who he is."
                className="min-h-0 w-full flex-1 resize-none bg-transparent px-5 py-4 text-[13.5px] leading-[1.75] outline-none"
              />

              <div className="border-border/60 flex shrink-0 items-center gap-3 border-t px-4 py-2.5">
                <span className="text-muted-foreground text-xs">
                  {dirty ? (
                    <>
                      Unsaved — he picks it up on his next message.{" "}
                      {/* "in the file", not just "chars". This counts every character you
                          typed, and the sentence three inches left says notes in <!-- --> are
                          free — so writing a 200-character note billed you 200 for nothing.
                          What it costs him is the number in the toolbar above, and that one
                          only moves on save, which is the honest thing for it to do. */}
                      <span className="tabular-nums">
                        {draft.length > current.body.length ? "+" : ""}
                        {(draft.length - current.body.length).toLocaleString()} chars in the file
                      </span>
                    </>
                  ) : (
                    <>
                      Saved. <span className="font-mono">⌘S</span> saves.
                      {parts.separable ? null : (
                        <> This one has notes in the middle of it, so it stays one box.</>
                      )}
                    </>
                  )}
                </span>
                <div className="flex-1" />
                {unsaved.length > 1 ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => void saveAll(unsaved)}
                  >
                    Save all {unsaved.length}
                  </Button>
                ) : null}
                {dirty ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setDrafts((held) => {
                        const { [current.name]: _gone, ...rest } = held;
                        return rest;
                      })
                    }
                  >
                    <RotateCcw className="size-3.5" />
                    Revert
                  </Button>
                ) : null}
                <Button
                  size="sm"
                  disabled={!dirty || busy}
                  onClick={() => void save(current, draft)}
                >
                  {busy ? <Loader2 className="size-4 animate-spin" /> : null}
                  Save
                </Button>
              </div>
            </div>
          ) : (
            <p className="text-muted-foreground p-6 text-sm">
              No fragments yet. Add one — anything you write here becomes part of who he is.
            </p>
          )}
        </div>
      )}

      {error ? (
        <p className="text-destructive border-border/60 shrink-0 border-t px-5 py-2 text-sm">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * One fragment in the rail.
 *
 * The switch lives here rather than in the editor header because the docstring's promise —
 * "turn one idea off to see what changed" — is a thing you do to the *set*, comparing one
 * against another, and it was previously six selections away. The number is what the fragment
 * costs him, not what the file weighs — see `promptChars`.
 */
function FragmentRow({
  fragment,
  selected,
  dirty,
  busy,
  onOpen,
  onToggle,
}: {
  fragment: PersonaFragment;
  selected: boolean;
  dirty: boolean;
  busy: boolean;
  onOpen: () => void;
  onToggle: (enabled: boolean) => void;
}) {
  return (
    <div
      className={cn(
        "group flex items-start gap-2 rounded-lg px-2 py-2 transition-colors",
        // Selection has to read louder than hover, or resting the pointer in the list makes some
        // other row look like the one you are editing.
        selected ? "bg-kith-soft ring-kith/20 ring-1" : "hover:bg-accent/40",
      )}
    >
      <label
        className="mt-0.5 shrink-0 cursor-pointer p-0.5"
        title={
          fragment.enabled
            ? "In his prompt — untick to see him without it"
            : "Not in his prompt. The file is still there, renamed with a leading underscore, which is also why it has moved to the end of this list."
        }
      >
        <input
          type="checkbox"
          checked={fragment.enabled}
          disabled={busy}
          className="accent-kith size-3.5 align-middle"
          aria-label={`${fragment.title} in his prompt`}
          onChange={(event) => onToggle(event.target.checked)}
        />
      </label>

      <button type="button" onClick={onOpen} aria-current={selected} className="min-w-0 flex-1 text-left">
        <span className="flex items-baseline gap-1.5">
          <span
            className={cn(
              "truncate text-sm",
              !fragment.enabled && "text-muted-foreground line-through decoration-1",
            )}
          >
            {fragment.title}
          </span>
          {dirty ? (
            <span
              aria-label="unsaved"
              title="Unsaved changes"
              className="bg-kith size-1.5 shrink-0 rounded-full"
            />
          ) : null}
          <span className="flex-1" />
          <span
            className="text-muted-foreground/60 shrink-0 font-mono text-[10px] tabular-nums"
            title={`${fragment.promptChars.toLocaleString()} of ${fragment.chars.toLocaleString()} characters reach him — the rest is notes to yourself`}
          >
            {fragment.promptChars.toLocaleString()}
          </span>
        </span>
        <span className="text-muted-foreground/50 block truncate font-mono text-[10px]">
          {fragment.name}
        </span>
      </button>
    </div>
  );
}

/**
 * The merged prompt, read-only — what he actually receives.
 *
 * `/api/persona` has always returned this and the interface has never shown it, which left
 * the screen's central claim unverifiable: you could edit six files and never see the one
 * thing they add up to. It is the server's merge rather than one recomputed here, so the
 * text on screen cannot drift from the text he gets — the comment stripping and the join
 * both live in `services/persona.py` and are not repeated in TypeScript.
 *
 * Which is also why unsaved drafts are called out rather than folded in: this is the saved
 * state, and quietly previewing something he would not receive would make the one honest
 * view on this screen the dishonest one.
 */
function MergedPrompt({ parts, unsaved }: { parts: PersonaPart[]; unsaved: number }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <p className="text-muted-foreground/70 border-border/60 shrink-0 border-b px-5 py-2 text-[11px]">
        The fragments joined in merge order with your notes stripped out — the exact text at the
        front of every request.
        {unsaved > 0 ? (
          <span className="text-kith">
            {" "}
            {unsaved} fragment{unsaved === 1 ? " has" : "s have"} unsaved changes, which are not in
            this yet.
          </span>
        ) : null}
      </p>
      <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
        <div className="space-y-6">
          {parts.map((part) => (
            <section key={part.name}>
              {/* Which file this came from. Without it the merge answers "what does he read"
                  and refuses to answer "so where do I change it", which is the next question
                  every single time. The rule is still no re-merging here: these are the
                  server's own pieces, in its own order, joined by the same blank line. */}
              <h3 className="text-muted-foreground/50 mb-1.5 font-mono text-[10px]">
                {part.name}
              </h3>
              <pre className="font-mono text-[12px] leading-[1.7] whitespace-pre-wrap">
                {part.text}
              </pre>
            </section>
          ))}
          {parts.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              Nothing is switched on, so his prompt has no persona in it at all.
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * The note, folded away above the prose.
 *
 * Shut by default and one line when shut, because it is the answer to "why does this fragment
 * exist" and you are usually here to change what it says instead. Open, it is a real field —
 * these run long, and the one on `35-how-you-spend-a-round.md` carries the measurements the
 * fragment was written from, which is worth keeping and worth keeping out of the prompt.
 */
function NoteField({
  note,
  open,
  onOpen,
  onChange,
}: {
  note: string;
  open: boolean;
  onOpen: (open: boolean) => void;
  onChange: (note: string) => void;
}) {
  return (
    <div className="border-border/60 bg-muted/20 shrink-0 border-b">
      <button
        type="button"
        onClick={() => onOpen(!open)}
        aria-expanded={open}
        className="hover:bg-accent/30 flex w-full items-center gap-2 px-5 py-1.5 text-left transition-colors"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "text-muted-foreground/40 size-3 shrink-0 transition-transform",
            open && "rotate-90",
          )}
        />
        <span className="text-muted-foreground/70 shrink-0 text-[10px] tracking-wide uppercase">
          Your note
        </span>
        {open ? null : (
          <span className="text-muted-foreground/50 min-w-0 flex-1 truncate text-[11px] italic">
            {note || "none"}
          </span>
        )}
        <span className="ms-auto shrink-0 pl-2 text-[10px] text-muted-foreground/40">
          not sent to him
        </span>
      </button>
      {open ? (
        <textarea
          value={note}
          spellCheck
          aria-label="Your note about this fragment — not sent to him"
          onChange={(event) => onChange(event.target.value)}
          placeholder="Why this fragment exists, what it was measured against — anything you want to remember and he does not need."
          className="text-muted-foreground h-28 w-full resize-y bg-transparent px-5 pb-3 text-[12.5px] leading-[1.65] outline-none"
        />
      ) : null}
    </div>
  );
}
