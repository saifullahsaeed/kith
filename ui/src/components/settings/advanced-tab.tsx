import { useCallback, useEffect, useState } from "react";
import { formatBytes } from "@/lib/bytes";
import {
  ChevronDown,
  FolderOpen,
  Loader2,
  RotateCcw,
  Search,
  TerminalSquare,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { numericInputClass as inputClass } from "@/components/ui/input";
import { openOnHost } from "@/lib/files";
import { cn } from "@/lib/utils";
import { StandingGrants } from "./standing-grants";
import {
  fetchTuning,
  pickFolder,
  resetTuning,
  saveTuning,
  setWorkspaceRoot,
  type Tunable,
  type TuningSnapshot,
} from "@/lib/backend";

/**
 * Everything else, with the consequences written down.
 *
 * These were environment variables and inlined constants — which for a packaged
 * desktop app means unreachable, since there is no shell to export anything in. If
 * this is going to be something other people run, "you can configure it by editing
 * the source" is not configurable.
 *
 * Every field, its label, its bounds and its explanation come from the server's
 * registry, so this component knows nothing about any particular setting and adding
 * one needs no change here. The help text is always visible rather than hidden behind
 * a tooltip: these are sharp, and a number whose consequence you have to hover to
 * discover is a trap.
 */
//: Short names for the tab strip. The group's own label is a sentence written to sit *above* its
//: settings and reads badly inside a tab — "Recognising work you already have" against "MCP".
const SHORT: Record<string, string> = {
  chat: "Chat",
  context: "Context",
  limits: "Limits",
  connections: "Connections",
  machine: "This computer",
  mcp: "MCP",
  stuck: "Duplicates",
};

export function AdvancedTab() {
  const confirm = useConfirm();
  const [snapshot, setSnapshot] = useState<TuningSnapshot | null>(null);
  const [draft, setDraft] = useState<Record<string, number | string | boolean>>(
    {},
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(false);
  //: Which group is on screen. Thirty-two settings under six headings was one page you scrolled
  //: past rather than read — the sections were the right division all along, they just were not a
  //: division you could land on.
  const [tab, setTab] = useState("");

  /**
   * Has this setting been touched — saved away from its default, *or* edited and not yet saved?
   *
   * Both, because "changed" means the same thing to the person either way and the filter is for
   * finding what you touched. Reading only `isDefault` — the server's view — meant editing a
   * setting left the Changed button disabled, which is the moment you most want it.
   */
  const isTouched = (knob: Tunable) =>
    !knob.isDefault || draft[knob.key] !== undefined;

  /** Does this knob survive the find box and the changed-only filter? */
  const matches = (knob: Tunable) => {
    if (onlyChanged && !isTouched(knob)) return false;
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    // The key and the environment variable are searchable too: someone who read a comment or a
    // stack trace knows `live_tool_chars`, not "Tool output kept in full".
    return `${knob.label} ${knob.help} ${knob.key} ${knob.env}`
      .toLowerCase()
      .includes(needle);
  };

  const load = useCallback(() => {
    fetchTuning()
      .then((next) => {
        setSnapshot(next);
        setDraft({});
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err)),
      );
  }, []);

  useEffect(load, [load]);

  const dirty = Object.keys(draft).length > 0;

  async function commit(action: () => Promise<TuningSnapshot>) {
    setBusy(true);
    setError("");
    try {
      setSnapshot(await action());
      setDraft({});
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  /**
   * Move him to another folder.
   *
   * Two things are deliberate. The chooser is the system's own, because a text field for a
   * path is how you get a typo pointed at nothing. And the split is stated up front: his
   * conversations travel with him (they have to — the index that lists them does not move,
   * so leaving them behind would make his whole history open empty), while the files he has
   * made stay put, because relocating gigabytes is not something a settings click should do.
   * Both halves are named in the confirm, since "everything he made is still in the old
   * folder" is the honest outcome and reads exactly like data loss when discovered later.
   */
  async function move(current: string) {
    setError("");
    const picked = await pickFolder(
      "Choose a folder for Kith to work in",
      current,
    );
    if (picked === null) {
      setError(
        "No desktop app running, so there's no folder chooser. Set KITH_WORKSPACE instead.",
      );
      return;
    }
    if (!picked || picked === current) return;
    const ok = await confirm({
      title: "Have Kith work in this folder?",
      subject: picked,
      description:
        `His conversations come with him. The files he has already made do not — those stay ` +
        `in ${current}, and you can copy them across yourself if you want them to follow.`,
      confirmLabel: "Work here",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await setWorkspaceRoot(picked);
      load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (error && !snapshot)
    return <p className="text-destructive text-sm">{error}</p>;
  if (!snapshot) {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-sm">
        <Loader2 className="size-4 animate-spin" /> Reading his settings…
      </p>
    );
  }

  //: Searching and the Changed filter cut *across* the groups, so they override the tab — a search
  //: that only looked inside the tab you happened to be on would answer "nothing matches" about a
  //: setting sitting one tab away, which is worse than no search.
  const seeking = Boolean(query.trim()) || onlyChanged;
  const active = tab || snapshot.groups[0]?.key || "";
  const groups = snapshot.groups
    .filter((group) => seeking || group.key === active)
    .map((group) => ({ ...group, settings: group.settings.filter(matches) }))
    .filter((group) => group.settings.length > 0);
  const shownCount = groups.reduce(
    (sum, group) => sum + group.settings.length,
    0,
  );
  //: Whether "This computer" is one of the groups on screen. The folder list and the standing
  //: grants hang off this rather than off `tab`, so a search that surfaces a machine setting
  //: brings them with it instead of showing settings about the machine with the machine hidden.
  const onMachine = groups.some((group) => group.key === "machine");
  const changedCount = snapshot.groups.reduce(
    (sum, group) => sum + group.settings.filter(isTouched).length,
    0,
  );

  return (
    <div className="space-y-8">
      {/*
        A find box and a filter, because forty settings is a list you search, not a list you read.
        Before these the only way to reach one was to scroll three screens past 7,655 characters
        of warnings — every one of them expanded, all at the same weight, so nothing stood out
        and nothing could be found.
      */}
      <div className="bg-background/95 sticky top-0 z-10 -mx-1 flex flex-wrap items-center gap-2 px-1 py-2 backdrop-blur">
        {/* Capped. `flex-1` was right when the pane was 768 wide and is not now the tab asks for
            the window: a search box the width of a desk is not easier to type in, it just moves
            the Changed filter to the far edge, a long way from the thing it filters. */}
        <div className="relative min-w-48 max-w-md flex-1">
          <Search className="text-muted-foreground/50 pointer-events-none absolute start-2.5 top-1/2 size-3.5 -translate-y-1/2" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`Search ${snapshot.groups.reduce((n, g) => n + g.settings.length, 0)} settings…`}
            className="w-full rounded-md border bg-transparent py-1.5 ps-8 pe-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
            aria-label="Search settings"
          />
        </div>
        <Button
          variant={onlyChanged ? "default" : "outline"}
          size="sm"
          onClick={() => setOnlyChanged((on) => !on)}
          disabled={!changedCount}
          title={
            changedCount
              ? "Show only the settings that differ from their default"
              : "Everything is at its default"
          }
        >
          Changed {changedCount ? `(${changedCount})` : ""}
        </Button>
      </div>

      {/* One tab per group. The short name is here rather than on the server because it exists for
          the strip alone: "Recognising work you already have" is the right sentence above the
          settings and the wrong one inside a tab. The count is worth showing — Connections has
          eleven and MCP has two, and knowing that before you click is the point of a tab strip.
          A dot means something in there differs from its default. */}
      <div
        className="-mx-1 flex flex-wrap gap-1 px-1"
        role="tablist"
        aria-label="Setting groups"
      >
        {snapshot.groups.map((group) => {
          const changed = group.settings.filter(isTouched).length;
          const on = !seeking && group.key === active;
          return (
            <button
              key={group.key}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() => {
                setTab(group.key);
                setQuery("");
                setOnlyChanged(false);
              }}
              title={group.blurb}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
                on
                  ? "bg-accent/70 text-foreground font-medium"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/30",
              )}
            >
              {SHORT[group.key] ?? group.label}
              <span className="text-muted-foreground/50 font-mono text-[10px] tabular-nums">
                {group.settings.length}
              </span>
              {changed ? (
                <span
                  className="bg-kith size-1.5 rounded-full"
                  aria-label="changed"
                />
              ) : null}
            </button>
          );
        })}
      </div>

      <p className="text-muted-foreground text-xs leading-relaxed">
        Every one of these changes how he behaves, and takes effect on his next
        turn — nothing here needs a restart. Each says what it does; open{" "}
        <strong className="font-medium">why</strong> on a row for what it costs
        you to get wrong.
      </p>

      {!shownCount ? (
        <p className="text-muted-foreground py-8 text-center text-sm">
          Nothing matches{" "}
          {query ? <code className="font-mono">{query}</code> : "that"}.
        </p>
      ) : null}

      {/*
        The two blocks below belong to one group and used to be drawn under every one of them.
        `groups` is already filtered to the tab you are on (or, while searching, to whatever
        matched) — but these sat outside that map, so "On this computer" and the standing grants
        appeared beneath the chat settings, the context settings and the rest, on a screen whose
        whole organising idea is that a tab is a subject. They are about this machine, so they
        show when this machine's settings do: on the "This computer" tab, and in a search that
        surfaces it.
      */}
      {groups.map((group) => (
        <section key={group.key}>
          <div className="mb-3">
            <h2 className="text-sm font-semibold">{group.label}</h2>
            <p className="text-muted-foreground text-xs leading-relaxed">
              {group.blurb}
            </p>
          </div>
          <div className="divide-y rounded-xl border">
            {group.settings.map((knob) => (
              <Row
                key={knob.key}
                knob={knob}
                draft={draft[knob.key]}
                onChange={(value) =>
                  setDraft((current) => {
                    // Typing a value back to what's stored isn't a change, so the Save
                    // button shouldn't claim there's something to save.
                    const next = { ...current };
                    // Blank is a real, saveable value for some settings — an unpinned provider,
                    // no fallback model, provider ordering left to OpenRouter — so it only
                    // counts as "no change" when blank is already what is stored. Treating ""
                    // as always-discard made those three impossible to clear from here.
                    const same = value === knob.value;
                    if (same || (value === "" && knob.value === ""))
                      delete next[knob.key];
                    else next[knob.key] = value;
                    return next;
                  })
                }
                onReset={() => commit(() => resetTuning([knob.key]))}
              />
            ))}
          </div>
        </section>
      ))}

      {onMachine ? (
        <section>
          <div className="mb-3 flex items-baseline gap-3">
            {/* Not "On this computer", which is the group's own heading three inches above it now
              that this only renders on that tab. Two identical headings in a column read as a
              rendering fault. */}
            <h2 className="text-sm font-semibold">Where his things live</h2>
            <p className="text-muted-foreground min-w-0 flex-1 text-xs">
              He works in real folders now, not a container. These are the ones,
              with what is in them — clickable, because a path you can only read
              is a path you have to retype.
            </p>
            <span className="text-muted-foreground/70 font-mono text-[11px] tabular-nums">
              {formatBytes(
                snapshot.paths.reduce(
                  (sum, path) => sum + (path.bytes ?? 0),
                  0,
                ),
              )}{" "}
              total
            </span>
          </div>
          <div className="divide-y rounded-xl border">
            {snapshot.paths.map((path) => (
              <div key={path.label} className="flex items-start gap-3 p-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm font-medium">{path.label}</span>
                    {path.bytes !== undefined ? (
                      <span className="text-muted-foreground/70 font-mono text-[10px] tabular-nums">
                        {formatBytes(path.bytes)}
                        {path.entries !== undefined
                          ? ` · ${path.entries} items`
                          : ""}
                      </span>
                    ) : null}
                    {path.env ? (
                      <code className="text-muted-foreground/50 ms-auto font-mono text-[10px]">
                        {path.env}
                      </code>
                    ) : null}
                  </div>
                  <code className="text-muted-foreground mt-0.5 block font-mono text-[11px] break-all">
                    {path.value}
                  </code>
                  {path.note ? (
                    <p className="text-muted-foreground/70 mt-0.5 text-[11px]">
                      {path.note}
                    </p>
                  ) : null}
                </div>
                {path.change ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="shrink-0"
                    disabled={busy || path.pinned}
                    onClick={() => void move(path.value)}
                    title={
                      path.pinned
                        ? `Fixed by ${path.env} in the environment — unset it to choose here.`
                        : "Pick a different folder for him to work in"
                    }
                  >
                    Change…
                  </Button>
                ) : null}
                {path.open ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="shrink-0"
                    onClick={() => void openOnHost(path.value, true)}
                    title="Show it in Finder"
                  >
                    <FolderOpen className="size-3.5" />
                    Reveal
                  </Button>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {onMachine ? <StandingGrants /> : null}

      {error ? <p className="text-destructive text-sm">{error}</p> : null}

      {/*
        Clearance for the bar below. It is `sticky`, so content scrolls *under* it — which meant
        the last row of the last group sat permanently behind the Save button, half-legible, with
        the tail of its help text poking out beneath. Fifty-six pixels of nothing is the whole fix.
      */}
      <div aria-hidden className="h-14" />

      <div className="sticky bottom-0 -mx-6 flex items-center gap-3 border-t bg-background/95 px-6 py-3 backdrop-blur">
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground"
          onClick={() => commit(() => resetTuning())}
          disabled={busy}
        >
          <RotateCcw className="size-3.5" /> Reset everything
        </Button>
        <div className="flex-1" />
        <span className="text-muted-foreground text-xs">
          {dirty ? `${Object.keys(draft).length} changed` : "Nothing to save."}
        </span>
        <Button
          onClick={() => commit(() => saveTuning(draft))}
          disabled={!dirty || busy}
        >
          {busy ? <Loader2 className="size-4 animate-spin" /> : null}
          {busy ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}

function Row({
  knob,
  draft,
  onChange,
  onReset,
}: {
  knob: Tunable;
  draft: number | string | boolean | undefined;
  onChange: (value: number | string | boolean) => void;
  onReset: () => void;
}) {
  const shown = draft ?? knob.value;
  const changed = draft !== undefined;
  // Open when you have touched it: the row you are editing is the one whose consequence you
  // should be reading, and that is exactly when hiding it would be the trap the old
  // always-expanded layout was guarding against.
  const [why, setWhy] = useState(false);
  const open = why || changed || !knob.isDefault;

  const [does, ...breaks] = splitHelp(knob.help);
  const consequence = breaks.join(" ");

  return (
    <div className="flex flex-wrap items-start gap-x-4 gap-y-1 p-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-sm font-medium">{knob.label}</span>
          {!knob.isDefault && !knob.fromEnv ? (
            <button
              type="button"
              onClick={onReset}
              className="text-muted-foreground/70 hover:text-kith text-[10px] underline decoration-dotted"
              title={`Back to ${knob.default}`}
            >
              changed — reset
            </button>
          ) : null}
          {knob.fromEnv ? (
            <span
              className="text-muted-foreground/70 inline-flex items-center gap-1 text-[10px]"
              title={`${knob.env} is set in the environment, so it wins over anything saved here.`}
            >
              <TerminalSquare className="size-3" /> set by {knob.env}
            </span>
          ) : null}
        </div>
        {/*
          What it does, always. What breaks, on request.

          This used to show the whole help string on all forty rows at once — 7,655 characters of
          warnings, every one as prominent as the label it belonged to, which is a document rather
          than a control panel. But hiding the consequence entirely would recreate the trap the
          old layout existed to prevent: a sharp number whose cost you only discover by hovering.
          The registry writes every help string as "what it does, then what breaks at the
          extremes" — 33 of 35 have that second sentence — so the split is already in the data.
        */}
        <p className="text-muted-foreground mt-0.5 max-w-prose text-xs leading-relaxed">
          {does}
          {consequence && !open ? (
            <button
              type="button"
              onClick={() => setWhy(true)}
              className="text-muted-foreground/60 hover:text-kith ms-1.5 inline-flex items-baseline gap-0.5 align-baseline text-[11px] underline decoration-dotted"
            >
              why <ChevronDown className="size-3 self-center" />
            </button>
          ) : null}
        </p>
        {consequence && open ? (
          <p className="text-muted-foreground/70 border-kith/30 mt-1 max-w-prose border-s-2 ps-2 text-xs leading-relaxed">
            {consequence}
          </p>
        ) : null}
      </div>

      <label className="flex shrink-0 items-center gap-2">
        <span
          className={
            knob.kind === "text" && !knob.choices?.length ? "w-64" : "w-32"
          }
        >
          <Field
            knob={knob}
            shown={shown}
            changed={changed}
            onChange={onChange}
          />
        </span>
        <span className="text-muted-foreground/70 w-14 text-[11px]">
          {knob.unit}
        </span>
      </label>
    </div>
  );
}

/**
 * "What it does." / "What breaks at the extremes."
 *
 * The registry's own description of the `help` field, so this reads a documented shape rather
 * than guessing at prose. Falls back to the whole string as the first part when there is only one
 * sentence — two knobs are like that, and they simply have no `why` to open.
 */
function splitHelp(help: string): string[] {
  const parts = help.split(/(?<=[.!?])\s+/);
  return parts.length > 1 ? parts : [help];
}

/**
 * The control a setting deserves, chosen by what it actually is.
 *
 * Everything used to be `type={kind === "text" ? "text" : "number"}`, which quietly meant every
 * boolean was a number box. `String(true)` is not a valid number, so the browser rendered the
 * field empty and fell back to the placeholder — three settings showing a greyed-out `true` that
 * read as unset, with no way to type `false`. You could not tell on from off, let alone change it.
 *
 * A knob with a handful of legal values gets a select for the same reason: a free-text box for
 * `prefer_provider_by` is a spelling test whose failure is silent, because the router ignores an
 * unknown value and the setting saves looking exactly as typed.
 */
function Field({
  knob,
  shown,
  changed,
  onChange,
}: {
  knob: Tunable;
  shown: number | string | boolean;
  changed: boolean;
  onChange: (value: number | string | boolean) => void;
}) {
  if (knob.kind === "bool") {
    const on = shown === true || shown === "true";
    return (
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={knob.label}
        disabled={knob.fromEnv}
        onClick={() => onChange(!on)}
        className={`flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
          on ? "bg-kith/80 border-kith" : "bg-foreground/10"
        } ${changed ? "ring-kith/40 ring-2" : ""}`}
      >
        {/* The knob. Translated rather than justified, so it slides instead of jumping. */}
        <span
          className={`bg-background size-4 rounded-full shadow-sm transition-transform ${
            on ? "translate-x-6" : "translate-x-1"
          }`}
        />
      </button>
    );
  }

  // `?.` because an older server sends no `choices` at all — see the note on the field. Falling
  // through to a plain text box is the right degradation: the value still shows and still saves,
  // and the server validates it either way.
  const choices = knob.choices ?? [];
  if (choices.length) {
    return (
      <select
        className={`${inputClass} text-left ${changed ? "border-kith" : ""}`}
        value={String(shown)}
        disabled={knob.fromEnv}
        onChange={(event) => onChange(event.target.value)}
        aria-label={knob.label}
      >
        {choices.map((choice) => (
          <option key={choice} value={choice}>
            {/* Blank is a real answer for some of these — "leave it to the provider" — and an
                empty option row is unclickable and unreadable. */}
            {choice || "— none —"}
          </option>
        ))}
      </select>
    );
  }

  return (
    <input
      type={knob.kind === "text" ? "text" : "number"}
      step={knob.kind === "float" ? 0.05 : 1}
      min={knob.min ?? undefined}
      max={knob.max ?? undefined}
      className={`${inputClass} ${changed ? "border-kith" : ""} ${knob.kind === "text" ? "text-left" : ""}`}
      value={String(shown)}
      disabled={knob.fromEnv}
      // The placeholder is normally the default, which for the several knobs whose default is
      // blank left an empty box with no hint in it — indistinguishable from a field that failed
      // to load. Say that blank is the setting.
      placeholder={String(knob.default) || "not set"}
      spellCheck={false}
      onChange={(event) => onChange(event.target.value)}
      aria-label={knob.label}
    />
  );
}

/** Bytes at the coarseness a person reads: nobody wants 1,721,233. */
