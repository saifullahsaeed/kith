import { useEffect, useState } from "react";
import { Check, Loader2, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cliStatus, installCli, type CliStatus } from "@/lib/backend/cli";

/**
 * The one-line offer to put `kith` on PATH, on the last screen of first run.
 *
 * **A card on the final step rather than a step of its own**, which is a deliberate reading of
 * "offer it during onboarding". A step is a thing you must get past; this is optional, and
 * making everyone click through a screen for a command line most people will not want on day
 * one is how a four-step flow becomes a five-step flow nobody finishes. It sits where the
 * other capabilities are already being listed, which is also the one moment somebody is
 * thinking about what this machine can do.
 *
 * It renders nothing at all when there is no shell to install from, rather than a disabled
 * button with an explanation. Onboarding is not the place to explain a capability you cannot
 * have; the settings page is, and it does.
 */
export function CliOffer() {
  const [status, setStatus] = useState<CliStatus | null>(null);
  const [working, setWorking] = useState(false);
  const [declined, setDeclined] = useState(false);

  useEffect(() => {
    void cliStatus().then(setStatus);
  }, []);

  if (!status?.available || declined) return null;

  if (status.installed) {
    return (
      <p className="flex items-center gap-2 rounded-lg border border-roam/30 bg-roam/5 p-3 text-xs">
        <Check className="size-3.5 shrink-0 text-roam" />
        <span>
          <code className="font-mono">kith</code> is on your PATH. Try{" "}
          <code className="font-mono">kith status</code> in a terminal.
        </span>
      </p>
    );
  }

  return (
    <div className="rounded-lg border p-3">
      <div className="flex items-start gap-3">
        <Terminal className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">Talk to him from a terminal</p>
          <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
            Installs the <code className="font-mono">kith</code> command, so scripts and other
            tools — Claude Code among them — can reach him. macOS will ask for your password.
            You can do this later in Settings.
          </p>
          <div className="mt-2.5 flex gap-2">
            <Button
              size="sm"
              disabled={working}
              onClick={() => {
                setWorking(true);
                void installCli().then((result) => {
                  setStatus(result.status);
                  setWorking(false);
                });
              }}
            >
              {working ? <Loader2 className="size-3.5 animate-spin" /> : "Install"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setDeclined(true)}>
              Not now
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
