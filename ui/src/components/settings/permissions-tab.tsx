import { ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";

import { StandingGrants } from "@/components/settings/standing-grants";
import { usePermissions } from "@/hooks/use-permissions";
import { MODE_LABELS, type PermissionMode } from "@/lib/backend";
import { cn } from "@/lib/utils";
import {
  PathValue,
  SettingRow,
  SettingRows,
  SettingSection,
} from "@/components/settings/setting-row";

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
    <div>
      <SettingSection
        title="When he asks"
        sub="He runs on your real computer. This is how much rope he has before he stops to check with you."
      >
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
                  "rounded-xl border p-3 text-left transition",
                  chosen
                    ? "border-kith/45 bg-kith-soft"
                    : "border-border/60 bg-card hover:border-border hover:bg-accent/30",
                )}
              >
                <span
                  className={cn(
                    "flex items-center gap-1.5 text-[13px] font-semibold",
                    chosen && "text-kith",
                  )}
                >
                  <Icon
                    className={cn(
                      "size-3.5",
                      one === "bypass" && "text-destructive",
                      one === "auto" && "text-roam",
                    )}
                  />
                  {MODE_LABELS[one].label}
                </span>
                <span className="text-muted-foreground mt-1.5 block text-[11.5px] leading-[1.45]">
                  {MODE_LABELS[one].hint}
                </span>
              </button>
            );
          })}
        </div>

        {/* Bypass needs the warning beside it, not in the card's own hint where it reads as one
            more feature of the option. The container that made it safe is gone. */}
        {mode === "bypass" ? (
          <div className="border-destructive/26 bg-destructive/8 mt-2.5 flex gap-2.5 rounded-xl border p-3 text-[11.5px] leading-[1.5]">
            <ShieldAlert className="text-destructive mt-px size-4 shrink-0" />
            <p>
              <b className="font-semibold">Bypass checks nothing.</b> It was safe when Kith ran
              inside a container, and that container is gone. On this machine he has your files
              and your shell.
            </p>
          </div>
        ) : null}

        {pending.length > 0 ? (
          <p className="text-muted-foreground mt-2.5 text-[11.5px]">
            {pending.length === 1
              ? "He is waiting on one answer right now."
              : `He is waiting on ${pending.length} answers right now.`}{" "}
            Those appear in the conversation, not here.
          </p>
        ) : null}
      </SettingSection>

      {/* Where "inside his folder" points. The mode's hints all turn on it, and it was readable
          only from a readiness check on another pane. */}
      {workspace?.root ? (
        <SettingSection title="Where he works" sub={'Everything the modes above call "his folder".'}>
          <SettingRows>
            <SettingRow
              label="His folder"
              help="Reading, writing and running here is free in every mode. Everything outside it follows the setting above."
            >
              <PathValue>{workspace.root}</PathValue>
            </SettingRow>
          </SettingRows>
        </SettingSection>
      ) : null}

      <StandingGrants />
    </div>
  );
}
