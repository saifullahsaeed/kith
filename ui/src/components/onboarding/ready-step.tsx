import { AlertTriangle, Check, CircleDashed, Info } from "lucide-react";

import { formatContext, formatPrice, type ModelOption, type ReadinessCheck } from "@/lib/backend";

/**
 * What he can and can't do, before the first conversation.
 *
 * Nothing here blocks anything — the connection is already saved by the time this
 * shows. It exists because a missing capability is invisible otherwise: someone whose
 * Docker isn't running would just find that he never uses his computer, and conclude
 * he's broken. Each degraded check says what is lost and what to do, so it can be
 * ignored deliberately rather than by accident.
 */
export function ReadyStep({
  providerLabel,
  model,
  chosenModel,
  checks,
  warnings,
}: {
  providerLabel: string;
  model: string;
  chosenModel: ModelOption | null;
  checks: ReadinessCheck[];
  warnings: string[];
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-xl border bg-card/70 p-4">
        <p className="text-muted-foreground text-xs">He'll think through</p>
        <p className="mt-0.5 font-medium">{providerLabel}</p>
        <p className="mt-2 font-mono text-sm break-all">{model}</p>
        {chosenModel ? (
          <p className="text-muted-foreground mt-1 font-mono text-[11px]">
            {formatPrice(chosenModel.promptPerMTok)} in ·{" "}
            {formatPrice(chosenModel.completionPerMTok)} out /Mtok ·{" "}
            {formatContext(chosenModel.context)} context
          </p>
        ) : null}
      </div>

      {warnings.length > 0 ? (
        <div className="space-y-2 rounded-lg border border-kith/30 bg-kith-soft/30 p-3">
          {warnings.map((warning) => (
            <p key={warning} className="flex gap-2 text-xs leading-relaxed">
              <Info className="mt-px size-3.5 shrink-0 text-kith" />
              <span>{warning}</span>
            </p>
          ))}
        </div>
      ) : null}

      <div className="divide-y rounded-xl border">
        {checks.length === 0 ? (
          <p className="text-muted-foreground p-3 text-sm">Couldn't read the capability checks.</p>
        ) : (
          checks.map((check) => <CheckRow key={check.key} check={check} />)
        )}
      </div>
    </div>
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
            {/* A one-line remedy is nearly always a command, and reads better as one. */}
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
