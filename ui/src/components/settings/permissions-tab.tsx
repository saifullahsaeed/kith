import { FolderOpen, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";

import { StandingGrants } from "@/components/settings/standing-grants";
import { usePermissions } from "@/hooks/use-permissions";
import { MODE_LABELS, type PermissionMode } from "@/lib/backend";
import { cn } from "@/lib/utils";

/**
 * What he may do on this computer without asking.
 *
 * ## Why this is a pane of its own
 *
 * Everything here existed and none of it was in settings.
 *
 * The **mode** was settable only from the title bar. That is the right place to *change* it —
 * it is a decision about the piece of work in front of you, not a preference you set once —
 * but it was the *only* place, and the readiness check that mentions it just said "switch to
 * Ask or Auto in the title bar", which is a settings screen pointing at somewhere else for an
 * answer it could have given.
 *
 * The **standing grants** were mounted at the bottom of Advanced, gated on a group key that
 * Advanced's own search box filters — so typing an unrelated word into that search made the
 * app's only revoke UI disappear. A list of everything he is permanently allowed to do should
 * not be reachable only by not searching.
 *
 * Grouping them is the point of the pane rather than a side effect: the mode decides what he
 * may do *without being asked*, and the grants are what you have already answered. They are two
 * halves of one question and they were three clicks apart.
 */

const MODE_ICONS: Record<PermissionMode, typeof ShieldCheck> = {
  ask: ShieldQuestion,
  auto: ShieldCheck,
  bypass: ShieldAlert,
};

export function PermissionsTab() {
  const { mode, setMode, workspace, pending } = usePermissions();

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-medium">When he asks</h3>
          <p className="text-muted-foreground max-w-prose text-[13px]">
            He runs on your real computer. This is how much rope he has before he stops to check
            with you — and it is the same control as the one in the title bar, which is where to
            reach for it mid-conversation.
          </p>
        </div>

        {/* Three cards rather than a select. The difference between these is a paragraph, not a
            word, and a dropdown shows you one hint at a time — so choosing between them means
            opening it three times and remembering. */}
        <div className="grid gap-2 sm:grid-cols-3">
          {(["ask", "auto", "bypass"] as PermissionMode[]).map((one) => {
            const Icon = MODE_ICONS[one];
            const chosen = mode === one;
            return (
              <button
                key={one}
                type="button"
                onClick={() => setMode(one)}
                aria-pressed={chosen}
                className={cn(
                  "rounded-lg border p-3 text-left transition",
                  chosen
                    ? "border-ring/60 bg-muted/60"
                    : "border-border/60 hover:border-border hover:bg-muted/30",
                )}
              >
                <span className="flex items-center gap-1.5 text-[13px] font-medium">
                  <Icon
                    className={cn(
                      "size-3.5",
                      one === "bypass" && "text-destructive",
                      one === "auto" && "text-roam",
                    )}
                  />
                  {MODE_LABELS[one].label}
                </span>
                <span className="text-muted-foreground mt-1 block text-[12px] leading-snug">
                  {MODE_LABELS[one].hint}
                </span>
              </button>
            );
          })}
        </div>

        {pending.length > 0 ? (
          <p className="text-muted-foreground text-[12px]">
            {pending.length === 1
              ? "He is waiting on one answer right now."
              : `He is waiting on ${pending.length} answers right now.`}{" "}
            Those appear in the conversation, not here.
          </p>
        ) : null}
      </section>

      {/* Where "inside his folder" points. The mode's hints all turn on it, and it was readable
          only from a readiness check on another pane. */}
      {workspace?.root ? (
        <section className="space-y-2">
          <h3 className="text-sm font-medium">Where he works</h3>
          <p className="text-muted-foreground max-w-prose text-[13px]">
            Everything the modes above call "his folder".
          </p>
          <p className="flex items-center gap-2 rounded-lg border border-border/60 px-3 py-2 font-mono text-[12px]">
            <FolderOpen className="text-muted-foreground size-3.5 flex-none" />
            <span className="truncate">{workspace.root}</span>
          </p>
        </section>
      ) : null}

      <StandingGrants />
    </div>
  );
}
