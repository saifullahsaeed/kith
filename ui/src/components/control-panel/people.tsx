import { User } from "lucide-react";
import { Markdown } from "@/components/files";
import { EditableText } from "@/components/ui/editable-text";
import { ItemMenu } from "@/components/ui/item-menu";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { DeleteButton, EmptyState, PageHeader } from "./chrome";
import { when } from "@/lib/dates";
import { initials, matches } from "./format";
import type { Handlers } from "./types";

/* ── Messages (chat bubbles from Kith) ──────────────────────────────────── */

export function People({
  snap,
  query,
  remove,
  update,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const items = snap.people.filter((p) => matches(query, p.name, p.relationship, p.profile));
  return (
    <>
      <PageHeader
        icon={<User className="size-5" />}
        color="teal"
        title="People"
        count={items.length}
        subtitle="Who he knows, and what he knows about them."
      />
      {items.length === 0 ? (
        <EmptyState icon={<User className="size-5" />}>
          Kith hasn't gotten to know anyone yet.
        </EmptyState>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {items.map((p) => (
            <ItemMenu
              key={p.id}
              title={p.name}
              copy={`${p.name} — ${p.relationship}\n\n${p.profile}`}
              onDelete={() => remove("person", p.id, p.name)}
            >
              <div className="group flex flex-col rounded-xl border border-border/70 bg-card/50 p-4 shadow-sm transition-all hover:border-teal-500/30 hover:shadow-md">
                <div className="flex items-center gap-3">
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-teal-500/12 text-sm font-semibold text-teal-500">
                    {initials(p.name)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold">{p.name}</div>
                    {p.relationship ? (
                      <div className="truncate text-xs text-muted-foreground">{p.relationship}</div>
                    ) : null}
                  </div>
                  <DeleteButton onClick={() => remove("person", p.id, p.name)} />
                </div>
                <div className="mt-3 border-t border-border/60 pt-3 text-sm leading-relaxed text-muted-foreground">
                  <EditableText
                    value={p.profile}
                    render={(v) => <Markdown>{v}</Markdown>}
                    onSave={(v) => update("person", p.id, { profile: v })}
                    multiline
                    placeholder="(nothing noted yet)"
                  />
                </div>
                <span className="mt-2 text-[11px] tabular-nums text-muted-foreground">
                  {when(p.updated_at)}
                </span>
              </div>
            </ItemMenu>
          ))}
        </div>
      )}
    </>
  );
}
