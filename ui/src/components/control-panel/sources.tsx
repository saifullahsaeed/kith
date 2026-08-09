import { useState } from "react";
import { FileText, Link as LinkIcon, Plus, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { Composer, DeleteButton, EmptyState, PageHeader } from "./chrome";
import { when } from "@/lib/dates";
import { matches } from "./format";
import { CHIP, FIELD } from "./types";
import type { Handlers } from "./types";

/* ── Sources (things fed to him to read) ────────────────────────────────── */

export function Sources({
  snap,
  query,
  remove,
  ingest,
}: {
  snap: BrainSnapshot;
  query: string;
  remove: Handlers["remove"];
  ingest: (input: { url?: string; text?: string }) => Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const items = snap.sources.filter((s) => matches(query, s.title, s.origin));
  const add = async () => {
    const v = value.trim();
    if (!v || busy) return;
    setBusy(true);
    setError("");
    try {
      await ingest(/^https?:\/\//i.test(v) ? { url: v } : { text: v });
      setValue("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "couldn't ingest that");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHeader
        icon={<FileText className="size-5" />}
        color="sky"
        title="Sources"
        count={items.length}
        subtitle="He reads it, remembers it, and recalls it on demand."
      />
      <Composer onSubmit={add}>
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Paste a link, or text for him to read…"
          className={`${FIELD} flex-1`}
        />
        <Button size="sm" onClick={add} disabled={busy}>
          {busy ? <RefreshCw className="size-4 animate-spin" /> : <Plus className="size-4" />}
          {busy ? "Reading…" : "Feed"}
        </Button>
      </Composer>
      {error ? <p className="-mt-3 mb-4 text-sm text-destructive">{error}</p> : null}
      {items.length === 0 ? (
        <EmptyState icon={<FileText className="size-5" />}>
          You haven't given him anything to read yet.
        </EmptyState>
      ) : (
        <div className="space-y-2">
          {items.map((s) => {
            const isLink = /^https?:\/\//i.test(s.origin);
            return (
              <div
                key={s.id}
                className="group flex items-center gap-3 rounded-xl border border-border/70 bg-card/50 p-4 shadow-sm transition-all hover:shadow-md"
              >
                <span
                  className={cn(
                    "flex size-9 shrink-0 items-center justify-center rounded-xl",
                    CHIP.sky,
                  )}
                >
                  {isLink ? <LinkIcon className="size-4" /> : <FileText className="size-4" />}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{s.title}</div>
                  <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                    {isLink ? (
                      <a
                        href={s.origin}
                        target="_blank"
                        rel="noreferrer"
                        className="truncate text-sky-500 hover:underline"
                      >
                        {s.origin}
                      </a>
                    ) : (
                      <span className="truncate">{s.origin}</span>
                    )}
                    <span className="shrink-0">· {s.chars.toLocaleString()} chars</span>
                    <span className="shrink-0">· {when(s.created_at)}</span>
                  </div>
                </div>
                <DeleteButton onClick={() => remove("source", s.id, s.title)} />
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
