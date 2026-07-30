import { useState } from "react";
import { Bell, Check, FolderOpen, HardDrive } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * The two things macOS will not let him do without your say-so.
 *
 * Neither can be granted from inside an app — that is the point of them — so this asks for
 * the one that can be prompted for, and walks you to the one that cannot. It is a step in
 * onboarding rather than a surprise later because both failures are silent: a notification
 * that never arrives and a folder that reads as empty look exactly like a bug in Kith.
 *
 * Notifications are asked for by *sending* one. macOS has no "request permission" call for
 * an app's first notification: the first one you post is the prompt. So the test
 * notification is not a nicety, it is the mechanism — and it doubles as proof, since you
 * either see it or you know immediately that something is off.
 *
 * Full Disk Access cannot be prompted for at all, only opened to. And the honest note about
 * it is which process to grant: his tools run in the Kith server, so that is the binary
 * macOS will ask about, not this window.
 */
export function AccessStep({ onDone }: { onDone: () => void }) {
  const [notified, setNotified] = useState(false);
  const [opened, setOpened] = useState(false);

  const testNotification = async () => {
    setNotified(true);
    try {
      // Posting one IS the permission prompt on macOS. Denied or allowed, it is answered
      // from here rather than at some unpredictable later moment.
      if ("Notification" in window) {
        if (Notification.permission === "default") await Notification.requestPermission();
        if (Notification.permission === "granted") {
          new Notification("Kith", { body: "This is how he'll reach you." });
        }
      }
    } catch {
      /* a refused prompt is an answer, not an error */
    }
  };

  return (
    <div className="space-y-3">
      <Row
        icon={<Bell className="size-4" />}
        title="Notifications"
        body="So he can tell you when something finishes, or when he needs you — including while the window is closed. He keeps working when it is."
        action={
          <Button
            variant={notified ? "outline" : "default"}
            size="sm"
            onClick={() => void testNotification()}
          >
            {notified ? <Check className="size-3.5" /> : <Bell className="size-3.5" />}
            {notified ? "Sent one" : "Allow and test"}
          </Button>
        }
      />

      <Row
        icon={<HardDrive className="size-4" />}
        title="Files beyond his own folder"
        body="He works in ~/Kith and needs nothing for that. Reading anything else — a PDF in Downloads, a spreadsheet on your Desktop — is macOS's decision, not his, and it has to be granted to the Kith server process."
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setOpened(true);
              window.open(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
                "_self",
              );
            }}
          >
            <FolderOpen className="size-3.5" />
            {opened ? "Opened" : "Open settings"}
          </Button>
        }
      />

      <p className="text-muted-foreground/70 text-xs">
        Both are optional and changeable later. He will ask before touching anything outside his own
        folder regardless — that is his own gate, not macOS's.
      </p>

      <div className="flex justify-end pt-1">
        <Button onClick={onDone}>Continue</Button>
      </div>
    </div>
  );
}

function Row({
  icon,
  title,
  body,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  action: React.ReactNode;
}) {
  return (
    <div className="bg-card/60 flex items-start gap-3 rounded-xl border p-3">
      <span className="text-muted-foreground mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">{body}</p>
      </div>
      <div className="shrink-0">{action}</div>
    </div>
  );
}
