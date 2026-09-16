import { useCallback, useEffect, useState } from "react";
import { FolderOpen, Puzzle, ShieldCheck, TerminalSquare, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  SettingRow,
  SettingRows,
  SettingSection,
} from "@/components/settings/setting-row";
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
    <SettingSection
      title="Allowed without asking"
      sub="Everything you answered “Always” to. He does these silently from now on, so this is the list worth knowing — and any of it can be taken back."
      action={
        grants.length ? (
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-destructive shrink-0"
            onClick={() => void forget()}
            disabled={busy}
          >
            Forget them
          </Button>
        ) : null
      }
    >

      {grants.length === 0 ? (
        <div className="text-muted-foreground flex items-center gap-2 rounded-xl border border-dashed px-3 py-4 text-xs">
          <ShieldCheck className="size-4 shrink-0 opacity-60" />
          <span>
            Nothing standing. He asks every time — except inside his own folder, where he never
            needed to.
          </span>
        </div>
      ) : (
        <SettingRows>
          {grants.map((grant) => (
            <Grant key={grant} signature={grant} onDrop={() => void drop(grant)} busy={busy} />
          ))}
        </SettingRows>
      )}

      {sessionGrants.length > 0 ? (
        <p className="text-muted-foreground/70 mt-2 text-[11px]">
          Plus {sessionGrants.length} you allowed just for this session, which{" "}
          {sessionGrants.length === 1 ? "is" : "are"} forgotten when Kith restarts.
        </p>
      ) : null}

      {error ? <p className="text-destructive mt-2 text-xs">{error}</p> : null}
    </SettingSection>
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
  /* Three namespaces now, and this row is the only place a person can take one back.
   *
   * A plugin grant is `plugin:<owner>:<seal>:<hash>`, and the hash is deliberately unreadable —
   * it covers the command line, its environment names and the boundary the program runs
   * inside, so that any change to what actually gets spawned stops matching and asks again.
   * Showing it raw would be a row of hex nobody can act on, so the owner is what is rendered
   * and the hash becomes the sentence underneath. Revoking this row is how you stop a server's
   * process without deleting its configuration. */
  const isPath = signature.startsWith("path:");
  const isPlugin = signature.startsWith("plugin:");
  const parts = signature.split(":");
  const owner = isPlugin ? (parts[1] ?? "").replace(/^user-/, "") : "";
  /* **Two shapes, and conflating them was a lie about access.**
   *
   * `plugin:<id>:*` is three segments and grants the plugin's declared *commands*.
   * `plugin:<id>:<seal>:<hash>` is four and grants its program the right to run.
   *
   * This only knew the second, so it read `parts[2]` for the seal — which on a command grant is
   * `*`, not `sealed`, and fell through to "with your program may run, with your full access".
   * A plugin that ships no program at all was therefore described as having a program running
   * with full access to the machine. Alarming, and false in both halves.
   *
   * `permissions._plugin_covers` is where the distinction is enforced: it requires three
   * segments on both sides, so a command grant structurally cannot cover a spawn one. */
  const isSpawn = isPlugin && parts.length === 4;
  const sealed = isSpawn && parts[2] === "sealed";
  const body = isPlugin ? owner : signature.slice(signature.indexOf(":") + 1);
  const Icon = isPath ? FolderOpen : isPlugin ? Puzzle : TerminalSquare;
  return (
    <SettingRow
      label={
        <span className="flex min-w-0 items-center gap-2">
          <Icon className="text-muted-foreground/60 size-3.5 shrink-0" />
          <code className="min-w-0 truncate font-mono text-[11.5px]">{body}</code>
        </span>
      }
      help={
        isPath
          ? "This and anything inside it"
          : isSpawn
            ? sealed
              ? "Its program may run, inside the boundary you approved"
              : "Its program may run, with nothing confining it"
            : isPlugin
              ? "Its commands may be called without asking you each time"
              : "This command, whenever he runs it"
      }
    >
      <button
        type="button"
        onClick={onDrop}
        disabled={busy}
        title="Forget this one — he'll ask again next time"
        aria-label={`Forget permission for ${body}`}
        // Shown on hover, but always reachable by keyboard: a control that only exists for
        // a mouse is not a control everyone has.
        className="text-muted-foreground/50 hover:text-destructive focus-visible:text-destructive shrink-0 transition"
      >
        <X className="size-3.5" />
      </button>
    </SettingRow>
  );
}
