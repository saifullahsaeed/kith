import { useCallback, useEffect, useState } from "react";
import { FolderOpen, ShieldCheck, TerminalSquare, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { fetchPermissions, revokeGrants, type PermissionState } from "@/lib/backend";

/**
 * What he may do without asking again.
 *
 * "Always allow" on a permission prompt wrote a grant to disk, honoured it forever, and
 * showed it nowhere. That is the wrong shape for a permission: you clicked it once, in a
 * hurry, about a folder you may not remember, and from then on the only evidence was the
 * absence of a prompt. A standing permission you cannot see is not one you granted, it is
 * one you lost track of — so this lists them, and every one of them can be taken back.
 *
 * Two lifetimes, kept apart on purpose. The ones from "Always" outlive restarts and are
 * the ones worth auditing; the ones from "Just this once" die with the process and are
 * shown only so the count in front of you matches the prompts you have been answering.
 *
 * Path grants extend to what is inside them — approving a folder approves its contents,
 * which is the behaviour that makes them usable and also the reason seeing them matters.
 * The list says so rather than leaving it to be inferred.
 */
export function StandingGrants() {
  const confirm = useConfirm();
  const [state, setState] = useState<PermissionState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetchPermissions()
      .then(setState)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(load, [load]);

  async function drop(signature: string) {
    setBusy(true);
    setError("");
    try {
      setState(await revokeGrants(signature));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function forget() {
    const count = state?.grants.length ?? 0;
    const ok = await confirm({
      title:
        count === 1
          ? "Forget this standing permission?"
          : `Forget all ${count} standing permissions?`,
      description:
        "He'll ask you again the next time he needs any of them. Nothing he has already " +
        "done is undone.",
      confirmLabel: "Forget them",
      destructive: true,
    });
    if (!ok) return;
    setBusy(true);
    setError("");
    try {
      setState(await revokeGrants());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!state) return null;
  const { grants, sessionGrants } = state;

  return (
    <section>
      <div className="mb-3 flex items-baseline gap-3">
        <h2 className="text-sm font-semibold">Allowed without asking</h2>
        <p className="text-muted-foreground min-w-0 flex-1 text-xs">
          Everything you answered “Always” to. He does these silently from now on, so this is the
          list worth knowing — and any of it can be taken back.
        </p>
        {grants.length ? (
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-destructive shrink-0"
            onClick={() => void forget()}
            disabled={busy}
          >
            Forget them
          </Button>
        ) : null}
      </div>

      {grants.length === 0 ? (
        <div className="text-muted-foreground flex items-center gap-2 rounded-xl border border-dashed px-3 py-4 text-xs">
          <ShieldCheck className="size-4 shrink-0 opacity-60" />
          <span>
            Nothing standing. He asks every time — except inside his own folder, where he never
            needed to.
          </span>
        </div>
      ) : (
        <div className="divide-y rounded-xl border">
          {grants.map((grant) => (
            <Grant key={grant} signature={grant} onDrop={() => void drop(grant)} busy={busy} />
          ))}
        </div>
      )}

      {sessionGrants.length > 0 ? (
        <p className="text-muted-foreground/70 mt-2 text-[11px]">
          Plus {sessionGrants.length} you allowed just for this session, which{" "}
          {sessionGrants.length === 1 ? "is" : "are"} forgotten when Kith restarts.
        </p>
      ) : null}

      {error ? <p className="text-destructive mt-2 text-xs">{error}</p> : null}
    </section>
  );
}

/** One grant, read back as the thing it permits rather than as its stored signature. */
function Grant({
  signature,
  onDrop,
  busy,
}: {
  signature: string;
  onDrop: () => void;
  busy: boolean;
}) {
  const isPath = signature.startsWith("path:");
  const body = signature.slice(signature.indexOf(":") + 1);
  const Icon = isPath ? FolderOpen : TerminalSquare;
  return (
    <div className="group flex items-start gap-2.5 px-3 py-2.5">
      <Icon className="text-muted-foreground/60 mt-0.5 size-3.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <code className="block font-mono text-[11px] break-all">{body}</code>
        <p className="text-muted-foreground/70 mt-0.5 text-[11px]">
          {isPath ? "This and anything inside it" : "This command, whenever he runs it"}
        </p>
      </div>
      <button
        type="button"
        onClick={onDrop}
        disabled={busy}
        title="Forget this one — he'll ask again next time"
        aria-label={`Forget permission for ${body}`}
        // Shown on hover, but always reachable by keyboard: a control that only exists for
        // a mouse is not a control everyone has.
        className="text-muted-foreground/40 hover:text-destructive focus-visible:text-destructive mt-0.5 shrink-0 opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}
