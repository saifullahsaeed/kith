import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, CircleDashed, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { MCPServers } from "@/components/settings/mcp-servers";
import { SearchStep } from "@/components/onboarding/search-step";
import {
  fetchLanguageServers,
  installLanguageServer,
  saveSearch,
  type LanguageServers,
  type ReadinessCheck,
  type SearchKind,
  type SearchOption,
  type SearchProbeOutcome,
  type SearchState,
} from "@/lib/backend";

/**
 * What he can reach beyond the model: search, his computer, his memory.
 *
 * Search is the only one that is a *choice* — the other two are installed or they
 * aren't, so they appear as status with a remedy rather than as options. Keeping them
 * on the same page anyway is the point: "what can he actually do right now" is one
 * question, and answering half of it here and half somewhere else would mean nobody
 * finds the half that matters.
 */
export function ToolsTab({
  search,
  options,
  checks,
  onSaved,
}: {
  search: SearchState;
  options: SearchOption[];
  checks: ReadinessCheck[];
  onSaved: () => void;
}) {
  const [kind, setKind] = useState<SearchKind>(search.kind);
  const [searxUrl, setSearxUrl] = useState(search.searxUrl ?? "");
  const [probe, setProbe] = useState<SearchProbeOutcome | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const dirty = kind !== search.kind || searxUrl !== (search.searxUrl ?? "");

  async function save() {
    setSaving(true);
    setError("");
    try {
      await saveSearch({ kind, searxUrl: searxUrl.trim() || undefined });
      setSaved(true);
      onSaved();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  // Search has its own control below, so showing its check too would say the same
  // thing twice — and the two could disagree until this page is re-read.
  const others = checks.filter((check) => check.key !== "search");

  return (
    <div className="space-y-6">
      <section>
        <div className="mb-3">
          <h2 className="text-sm font-semibold">Web search</h2>
          <p className="text-muted-foreground text-xs">
            How he looks things up on his own. This decides who pays for it, and who can see it.
          </p>
        </div>
        <SearchStep
          options={options}
          selected={kind}
          searxUrl={searxUrl}
          onSelect={(next) => {
            setKind(next);
            setSaved(false);
            setError("");
          }}
          onSearxUrl={(url) => {
            setSearxUrl(url);
            setSaved(false);
          }}
          onProbe={setProbe}
        />

        {error ? <p className="text-destructive mt-3 text-sm">{error}</p> : null}

        <div className="mt-4 flex items-center gap-3 border-t pt-4">
          <span className="text-muted-foreground text-xs">
            {saved && !dirty
              ? "Saved. He'll search this way from now on."
              : dirty
                ? "Unsaved change."
                : "Nothing to save."}
          </span>
          <div className="flex-1" />
          <Button onClick={save} disabled={!dirty || saving}>
            {saving ? <Loader2 className="size-4 animate-spin" /> : null}
            {/* A blocked instance is still a valid choice — it recovers — so this
                reports the probe rather than obeying it. */}
            {saving ? "Saving…" : probe?.working === false ? "Save anyway" : "Save"}
          </Button>
        </div>
      </section>

      <MCPServers />

      <CodeIntelligence />

      <section>
        <div className="mb-3">
          <h2 className="text-sm font-semibold">Everything else</h2>
          <p className="text-muted-foreground text-xs">
            Not choices — either installed or not. He works without them, just less well.
          </p>
        </div>
        <div className="divide-y rounded-xl border">
          {others.length === 0 ? (
            <p className="text-muted-foreground p-3 text-sm">Couldn't read the checks.</p>
          ) : (
            others.map((check) => <CheckRow key={check.key} check={check} />)
          )}
        </div>
      </section>
    </div>
  );
}

/**
 * Which languages this folder is written in, and what can answer questions about meaning.
 *
 * Reading code as *structure* — outlines, the repo map, finding a name by what it is —
 * ships with the app and always works. Reading it for *meaning* needs a language server,
 * which most machines do not have. Two tiers, and this section only exists for the second.
 *
 * One click, and no permission dialog: the gate is there to stop him changing your machine
 * without asking, and pressing this button is the asking. It installs only what this folder
 * is written in — never every language — into a folder you can delete.
 *
 * Go, Rust, Ruby and C come from their own package managers, so those show the command
 * instead of a button. Fetching four package managers into our own folder to save one line
 * of typing would be fighting the tools rather than using them.
 */
function CodeIntelligence() {
  const [state, setState] = useState<LanguageServers | null>(null);
  const [busy, setBusy] = useState("");
  const [failed, setFailed] = useState("");

  const read = useCallback(() => {
    void fetchLanguageServers()
      .then(setState)
      .catch(() => setState({ root: "", languages: [] }));
  }, []);

  useEffect(read, [read]);

  async function install(family: string) {
    setBusy(family);
    setFailed("");
    try {
      await installLanguageServer(family);
      read();
    } catch (err: unknown) {
      setFailed(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  // Nothing to say about a folder with no code in it, and an empty box saying so is worse
  // than no box — this page is already long.
  if (!state || state.languages.length === 0) return null;

  return (
    <section>
      <div className="mb-3">
        <h2 className="text-sm font-semibold">Understanding code</h2>
        <p className="text-muted-foreground text-xs">
          He reads structure — outlines, the map of a repository, where a name is used — with
          nothing installed. Answering <em>who calls this</em> and <em>what breaks if I rename
          it</em> needs a language server for that language.
        </p>
      </div>
      <div className="divide-y rounded-xl border">
        {state.languages.map((one) => (
          <div key={one.family} className="flex items-start gap-3 p-3">
            <span
              className={`mt-0.5 shrink-0 ${one.served ? "text-roam" : "text-muted-foreground/60"}`}
            >
              {one.served ? <Check className="size-4" /> : <CircleDashed className="size-4" />}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium capitalize">
                {one.family}
                <span className="text-muted-foreground ml-2 text-xs font-normal">
                  {one.files} file{one.files === 1 ? "" : "s"}
                </span>
              </p>
              <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
                {one.served
                  ? "A language server is available — the semantic tools work here."
                  : one.installable
                    ? `No language server. He can fetch one (${one.size}).`
                    : "No language server. This one comes from its own package manager:"}
              </p>
              {!one.served && !one.installable && one.manual ? (
                <p className="text-muted-foreground/80 mt-1.5 flex gap-1.5 text-xs">
                  <AlertTriangle className="mt-px size-3 shrink-0" />
                  <code className="bg-muted rounded px-1.5 py-0.5 font-mono text-[11px]">
                    {one.manual}
                  </code>
                </p>
              ) : null}
            </div>
            {!one.served && one.installable ? (
              <Button
                variant="outline"
                size="sm"
                className="shrink-0"
                disabled={busy !== ""}
                onClick={() => void install(one.family)}
              >
                {busy === one.family ? <Loader2 className="size-3.5 animate-spin" /> : null}
                {busy === one.family ? "Installing…" : "Install"}
              </Button>
            ) : null}
          </div>
        ))}
      </div>
      {failed ? <p className="text-destructive mt-2 text-xs">{failed}</p> : null}
      {state.root ? (
        <p className="text-muted-foreground/70 mt-2 text-[11px]">Looked at {state.root}</p>
      ) : null}
    </section>
  );
}

function CheckRow({ check }: { check: ReadinessCheck }) {
  const ok = check.health === "ok";
  return (
    <div className="flex gap-3 p-3">
      <span className={`mt-0.5 shrink-0 ${ok ? "text-roam" : "text-muted-foreground/60"}`}>
        {ok ? <Check className="size-4" /> : <CircleDashed className="size-4" />}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium">{check.title}</p>
        <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">{check.summary}</p>
        {check.remedy ? (
          <p className="text-muted-foreground/80 mt-1.5 flex gap-1.5 text-xs leading-relaxed">
            <AlertTriangle className="mt-px size-3 shrink-0" />
            {isCommand(check.remedy) ? (
              <code className="bg-muted rounded px-1.5 py-0.5 font-mono text-[11px]">
                {check.remedy}
              </code>
            ) : (
              <span>{check.remedy}</span>
            )}
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** A shell command, as opposed to a sentence about what to do. */
function isCommand(remedy: string): boolean {
  return !remedy.includes(" ") || /^(ollama|docker|brew|npm|make) /.test(remedy);
}
