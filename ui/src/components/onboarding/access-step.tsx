import { useState } from "react";
import { Bell, Check, FolderOpen, ShieldCheck, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { openSettingsPane, sendTestNotification } from "@/lib/backend";

type Outcome = "idle" | "working" | "ok" | "unavailable";

/**
 * The one thing worth asking for up front.
 *
 * This step asked for Full Disk Access too, and that was wrong. Full Disk Access is a
 * blanket grant over every file on the machine, and he does not need it: he works in
 * ~/Kith, which needs no permission at all, and macOS already asks — per folder, at the
 * moment of the first touch — when anything reaches into Desktop, Documents or Downloads.
 * Asking for the blanket version up front trades a prompt you would have seen anyway,
 * about a folder you actually care about, for one enormous grant made before anything has
 * happened. That is the opposite of informed.
 *
 * So what is left is notifications, which genuinely cannot happen without being asked, and
 * an explanation of the two gates that need nothing from you now.
 */
export function AccessStep({ onDone }: { onDone: () => void }) {
  const [notified, setNotified] = useState<Outcome>("idle");

  const test = async () => {
    setNotified("working");
    // The first notification an app posts IS the macOS prompt; there is no separate request
    // call. So this both asks and proves, in one click.
    setNotified((await sendTestNotification().catch(() => false)) ? "ok" : "unavailable");
  };

  return (
    <div className="space-y-3">
      <div className="bg-card/60 rounded-xl border p-3">
        <div className="flex items-center gap-2.5">
          <Bell className="text-muted-foreground size-4 shrink-0" />
          <p className="min-w-0 flex-1 text-sm font-medium">Notifications</p>
          <Button
            variant={notified === "ok" ? "outline" : "default"}
            size="sm"
            className="w-36 shrink-0"
            disabled={notified === "working"}
            onClick={() => void test()}
          >
            {notified === "ok" ? <Check className="size-3.5" /> : <Bell className="size-3.5" />}
            {notified === "ok" ? "Working" : notified === "working" ? "Sending…" : "Allow and test"}
          </Button>
        </div>
        <p className="text-muted-foreground mt-1.5 ps-7 text-xs leading-relaxed">
          So he can tell you when something finishes, or when he needs you — including while the
          window is closed. He keeps working when it is.
        </p>
        {notified === "ok" ? (
          <p className="text-kith mt-1.5 flex items-start gap-1.5 ps-7 text-xs">
            <Check className="mt-0.5 size-3.5 shrink-0" />
            One just went out. If you saw it, this is done.
          </p>
        ) : null}
        {notified === "unavailable" ? (
          <p className="text-muted-foreground mt-1.5 flex items-start gap-1.5 ps-7 text-xs">
            <TriangleAlert className="text-destructive mt-0.5 size-3.5 shrink-0" />
            <span>
              Nothing went out. Notifications need Kith running in the desktop app, and macOS has to
              allow it.{" "}
              <button
                type="button"
                className="text-foreground underline underline-offset-2"
                onClick={() => void openSettingsPane("notifications")}
              >
                Open Notification Center settings
              </button>
              .
            </span>
          </p>
        ) : null}
      </div>

      <div className="bg-card/40 rounded-xl border border-dashed p-3">
        <div className="flex items-center gap-2.5">
          <FolderOpen className="text-muted-foreground size-4 shrink-0" />
          <p className="min-w-0 flex-1 text-sm font-medium">Files — nothing to grant</p>
          <span className="text-muted-foreground/60 shrink-0 font-mono text-[11px]">~/Kith</span>
        </div>
        <p className="text-muted-foreground mt-1.5 ps-7 text-xs leading-relaxed">
          He works in his own folder, which needs no permission. If he ever reaches further — a PDF
          in Downloads, a spreadsheet on your Desktop — macOS asks you then, about that folder, and
          you can say no. There is no blanket access to hand over, and he does not want one.
        </p>
      </div>

      <div className="bg-card/40 rounded-xl border border-dashed p-3">
        <div className="flex items-center gap-2.5">
          <ShieldCheck className="text-muted-foreground size-4 shrink-0" />
          <p className="min-w-0 flex-1 text-sm font-medium">And he asks too</p>
          <span className="text-muted-foreground/60 shrink-0 font-mono text-[11px]">Ask mode</span>
        </div>
        <p className="text-muted-foreground mt-1.5 ps-7 text-xs leading-relaxed">
          Before macOS gets a say, he has his own gate: anything outside his folder, or anything
          destructive, waits for a yes from you in the conversation. Changeable from the title bar.
        </p>
      </div>

      <div className="flex justify-end pt-1">
        <Button onClick={onDone}>Continue</Button>
      </div>
    </div>
  );
}
