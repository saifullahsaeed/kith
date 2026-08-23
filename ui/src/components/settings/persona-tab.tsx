import { useCallback, useEffect, useRef, useState } from "react";
import { FileText, FolderOpen, Loader2, Plus, RotateCcw, Trash2 } from "lucide-react";

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
  type PersonaState,
} from "@/lib/backend";
import { openOnHost } from "@/lib/files";
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
 * The character count is on screen throughout because it is the one number with a
 * consequence: every request he ever makes carries all of this, and it is the region that
 * caches, so it is cheap per-request and expensive per-idea.
 */
export function PersonaTab() {
  const [state, setState] = useState<PersonaState | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const prompt = usePrompt();
  const [error, setError] = useState("");
  const confirm = useConfirm();

  const load = useCallback((keep?: string) => {
    fetchPersona()
      .then((next) => {
        setState(next);
        const pick =
          keep && next.fragments.some((one) => one.name === keep)
            ? keep
            : (next.fragments[0]?.name ?? "");
        setSelected(pick);
        setDraft(next.fragments.find((one) => one.name === pick)?.body ?? "");
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => load(), [load]);

  // Cmd-S. It is a text editor; muscle memory arrives before the mouse does, and without
  // this the browser's own Save dialog appears over the top of it.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveRef.current?.();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const current = state?.fragments.find((one) => one.name === selected) ?? null;
  const dirty = current !== null && draft !== current.body;
  // A ref so the key handler is bound once and still sees the current fragment.
  const saveRef = useRef<(() => void) | null>(null);
  saveRef.current =
    current && dirty && !busy
      ? () => void act(() => savePersonaFragment(current.name, draft), current.name)
      : null;

  const act = async (work: () => Promise<unknown>, keep?: string) => {
    setBusy(true);
    setError("");
    try {
      await work();
      load(keep);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const choose = (fragment: PersonaFragment) => {
    if (dirty && !window.confirm("Discard your unsaved changes to this fragment?")) return;
    setSelected(fragment.name);
    setDraft(fragment.body);
  };

  if (!state) {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-sm">
        <Loader2 className="size-4 animate-spin" /> Reading his persona…
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {/* Title, size and Reveal on one line; the explanation on its own. They were all
          competing for one row, and in a narrow window the sentence wrapped three times
          around the number. */}
      <div>
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold">Who he is</h2>
          <span
            className="text-muted-foreground/70 font-mono text-[11px] tabular-nums"
            title="Every request carries all of this. It is also the part that caches, so it costs once per five minutes rather than once per message."
          >
            {state.chars.toLocaleString()} chars · ~{Math.round(state.chars / 4).toLocaleString()}{" "}
            tokens
          </span>
          <div className="flex-1" />
          <Button variant="outline" size="sm" onClick={() => void openOnHost(state.folder, true)}>
            <FolderOpen className="size-3.5" />
            Reveal
          </Button>
        </div>
        <p className="text-muted-foreground mt-1 text-xs">
          Files merged in filename order — the number prefix <em>is</em> the order, so renaming is
          how you move something earlier or later.
        </p>
      </div>

      <div className="grid gap-5 lg:grid-cols-[14rem_1fr]">
        <div className="space-y-1">
          {state.fragments.map((fragment) => (
            <button
              key={fragment.name}
              type="button"
              onClick={() => choose(fragment)}
              className={cn(
                "hover:bg-accent/60 flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors",
                fragment.name === selected && "bg-kith-soft/50",
                !fragment.enabled && "opacity-50",
              )}
            >
              <FileText className="text-muted-foreground mt-0.5 size-3.5 shrink-0" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">{fragment.title}</span>
                <span className="text-muted-foreground/60 block font-mono text-[10px] tabular-nums">
                  {fragment.name} · {fragment.chars.toLocaleString()}
                  {fragment.enabled ? "" : " · off"}
                </span>
              </span>
            </button>
          ))}

          <Button
            variant="outline"
            size="sm"
            className="mt-2 w-full"
            disabled={busy}
            onClick={() => {
              void prompt({
                title: "Name the fragment",
                description: "The number decides where it lands in the merge.",
                initial: "50-my-fragment.md",
                confirmLabel: "Add",
              }).then((name) => {
                if (!name) return;
                void act(() => addPersonaFragment(name, "<!-- why this exists -->\n"), name);
              });
            }}
          >
            <Plus className="size-3.5" />
            Add a fragment
          </Button>
        </div>

        {current ? (
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <code className="text-muted-foreground min-w-0 flex-1 truncate font-mono text-[11px]">
                {current.name}
              </code>
              <label className="text-muted-foreground flex cursor-pointer items-center gap-1.5 text-[11px] select-none">
                <input
                  type="checkbox"
                  checked={current.enabled}
                  className="accent-kith size-3.5"
                  onChange={(event) =>
                    void act(
                      () => setPersonaFragmentEnabled(current.name, event.target.checked),
                      // Disabling renames the file, so the selection has to follow it.
                      event.target.checked
                        ? current.name.replace(/(^|\/)_/, "$1")
                        : current.name.replace(/(^|\/)(?!_)/, "$1_"),
                    )
                  }
                />
                In his prompt
              </label>
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => {
                  void prompt({
                    title: "Rename this fragment",
                    initial: current.name,
                  }).then((next) => {
                    if (next && next !== current.name) {
                      void act(() => renamePersonaFragment(current.name, next), next);
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
                onClick={async () => {
                  const ok = await confirm({
                    title: "Delete this fragment?",
                    subject: current.name,
                    description: "It is removed from the folder. There is no undo.",
                    destructive: true,
                  });
                  if (ok) void act(() => deletePersonaFragment(current.name));
                }}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>

            {/* Prose, not code: this is writing, and a 12px monospace box discourages
                writing in it. Spellcheck on for the same reason — his persona is English. */}
            <textarea
              value={draft}
              spellCheck
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Write in the second person — you are telling him who he is."
              className="focus-visible:border-ring focus-visible:ring-ring/40 h-[30rem] w-full resize-y rounded-xl border bg-transparent px-4 py-3.5 text-[13.5px] leading-[1.75] outline-none focus-visible:ring-2"
            />

            <div className="flex items-center gap-3">
              <span className="text-muted-foreground text-xs">
                {dirty ? (
                  <>
                    Unsaved — he picks it up on his next message.{" "}
                    <span className="tabular-nums">
                      {draft.length > current.body.length ? "+" : ""}
                      {(draft.length - current.body.length).toLocaleString()} chars
                    </span>
                  </>
                ) : (
                  <>
                    Saved. Anything in <code>{"<!-- -->"}</code> is a note to yourself — stripped
                    before he reads it. <span className="font-mono">⌘S</span> saves.
                  </>
                )}
              </span>
              <div className="flex-1" />
              {dirty ? (
                <Button variant="ghost" size="sm" onClick={() => setDraft(current.body)}>
                  <RotateCcw className="size-3.5" />
                  Revert
                </Button>
              ) : null}
              <Button
                size="sm"
                disabled={!dirty || busy}
                onClick={() =>
                  void act(() => savePersonaFragment(current.name, draft), current.name)
                }
              >
                {busy ? <Loader2 className="size-4 animate-spin" /> : null}
                Save
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">
            No fragments yet. Add one — anything you write here becomes part of who he is.
          </p>
        )}
      </div>

      {error ? <p className="text-destructive text-sm">{error}</p> : null}
    </div>
  );
}
