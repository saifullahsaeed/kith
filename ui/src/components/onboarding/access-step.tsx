import { useState } from "react";
import { Bell, Check, FolderOpen, HardDrive, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { openSettingsPane, sendTestNotification } from "@/lib/backend";

type Outcome = "idle" | "working" | "ok" | "unavailable";

/**
 * The two things macOS decides, and proof of whether they took.
 *
 * Neither can be granted from inside an app — that is the point of them — so this asks for
 * the one that can be prompted for and walks you to the one that cannot. It is a step in
 * onboarding because both failures are silent: a notification that never arrives and a
 * folder that reads as empty look exactly like a bug in Kith.
 *
 * Both actions go through the server to the desktop shell rather than being done here, and
 * both were quietly broken when they were done here. The browser's Notification API reports
 * permission "granted", throws nothing, and macOS drops the notification, because a
 * notification from a page has no app identity to attribute — only the shell's main process
 * has one. And `window.open("x-apple.systempreferences:…")` was swallowed by the shell's
 * navigation hardening, which allows http, https and mailto and nothing else.
 *
 * So each button reports what actually happened. "Sent one" when nothing appeared was the
 * worst of the three possible outcomes: it looked like success and taught you nothing.
 */
export function AccessStep({ onDone }: { onDone: () => void }) {
  const [notified, setNotified] = useState<Outcome>("idle");
  const [opened, setOpened] = useState<Outcome>("idle");

  const test = async () => {
    setNotified("working");
    // The first notification an app posts IS the macOS permission prompt; there is no
    // separate request call. So this both asks and proves, in one click.
    setNotified((await sendTestNotification().catch(() => false)) ? "ok" : "unavailable");
  };

  const openFolders = async () => {
    setOpened("working");
    setOpened((await openSettingsPane("fullDisk").catch(() => false)) ? "ok" : "unavailable");
  };

  return (
    <div className="space-y-3">
      <Row
        icon={<Bell className="size-4" />}
        title="Notifications"
        body="So he can tell you when something finishes, or when he needs you — including while the window is closed. He keeps working when it is."
        outcome={notified}
        ok="Sent — check the top-right of your screen"
        unavailable="Nothing appeared. Notifications need Kith running in the desktop app, and macOS has to allow it in Notification Center."
        action={
          <Button
            variant={notified === "ok" ? "outline" : "default"}
            size="sm"
            className="w-36"
            disabled={notified === "working"}
            onClick={() => void test()}
          >
            {notified === "ok" ? <Check className="size-3.5" /> : <Bell className="size-3.5" />}
            {notified === "ok"
              ? "Sent one"
              : notified === "working"
                ? "Sending…"
                : "Allow and test"}
          </Button>
        }
      />

      <Row
        icon={<HardDrive className="size-4" />}
        title="Files beyond his own folder"
        body="He works in ~/Kith and needs nothing for that. Reading anything else — a PDF in Downloads, a spreadsheet on your Desktop — is macOS's decision, and it is granted to the Kith server process rather than to this window."
        outcome={opened}
        ok="System Settings is open — add the Kith server under Full Disk Access"
        unavailable="Couldn't open it from here. Open System Settings → Privacy & Security → Full Disk Access yourself."
        action={
          <Button
            variant="outline"
            size="sm"
            className="w-36"
            disabled={opened === "working"}
            onClick={() => void openFolders()}
          >
            <FolderOpen className="size-3.5" />
            {opened === "ok" ? "Opened" : "Open settings"}
          </Button>
        }
      />

      <p className="text-muted-foreground/70 text-xs">
        Both are optional and changeable later. He asks before touching anything outside his own
        folder regardless — that is his own gate, not macOS's.
      </p>

      <div className="flex justify-end pt-1">
        <Button onClick={onDone}>Continue</Button>
      </div>
    </div>
  );
}

/**
 * One row: what it is, why, the button, and what came of pressing it.
 *
 * The button sits on the title line rather than centred against the paragraph — against
 * three lines of text a vertically-centred button reads as floating, which is what was
 * wrong with the first version of this.
 */
function Row({
  icon,
  title,
  body,
  action,
  outcome,
  ok,
  unavailable,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  action: React.ReactNode;
  outcome: Outcome;
  ok: string;
  unavailable: string;
}) {
  return (
    <div className="bg-card/60 rounded-xl border p-3">
      <div className="flex items-center gap-2.5">
        <span className="text-muted-foreground shrink-0">{icon}</span>
        <p className="min-w-0 flex-1 text-sm font-medium">{title}</p>
        <div className="shrink-0">{action}</div>
      </div>
      <p className="text-muted-foreground mt-1.5 ps-7 text-xs leading-relaxed">{body}</p>
      {outcome === "ok" ? (
        <p className="text-kith mt-1.5 flex items-start gap-1.5 ps-7 text-xs">
          <Check className="mt-0.5 size-3.5 shrink-0" />
          {ok}
        </p>
      ) : null}
      {outcome === "unavailable" ? (
        <p className="text-muted-foreground mt-1.5 flex items-start gap-1.5 ps-7 text-xs">
          <TriangleAlert className="text-destructive mt-0.5 size-3.5 shrink-0" />
          {unavailable}
        </p>
      ) : null}
    </div>
  );
}
