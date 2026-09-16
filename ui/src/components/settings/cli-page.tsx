import { useCallback, useEffect, useState } from "react";
import { Check, Loader2, Terminal, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cliStatus, installCli, type CliStatus } from "@/lib/backend/cli";

/**
 * Putting `kith` on PATH, from the one screen where somebody is already deciding how this
 * machine is set up.
 *
 * The page exists because a binary inside an app bundle is not a command line. Something has
 * to link it somewhere a shell will look, and on macOS that means /usr/local/bin and an
 * administrator password — `~/.local/bin` is not on PATH by default and finishing that install
 * means editing somebody's shell profile.
 *
 * Four states, and the middle two are the ones a simpler page would collapse and get wrong:
 * installed, not installed, **stale** (a link that points at a different Kith — needs
 * replacing, not creating), and **unavailable** (no desktop shell, so there is nothing to link
 * from and the button would be a lie).
 */
export function CliPage() {
  const [status, setStatus] = useState<CliStatus | null>(null);
  const [working, setWorking] = useState(false);
  const [problem, setProblem] = useState("");

  const refresh = useCallback(async () => {
    setStatus(await cliStatus());
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = useCallback(
    async (remove: boolean) => {
      setWorking(true);
      setProblem("");
      const result = await installCli(remove);
      setStatus(result.status);
      // A dismissed password dialog is an answer, not a fault. Saying "installation failed"
      // to somebody who just decided not to install is the app arguing with them.
      if (!result.ok && !result.cancelled) setProblem(result.error || "it did not work");
      setWorking(false);
    },
    [],
  );

  if (!status) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Checking…
      </div>
    );
  }

  return (
    <div className="max-w-2xl space-y-6 p-6">
      <div className="space-y-2">
        <h2 className="flex items-center gap-2 text-base font-medium">
          <Terminal className="size-4" />
          The kith command
        </h2>
        <p className="text-sm text-muted-foreground">
          Talk to Kith from a terminal, and let other tools talk to him — Claude Code, a
          script, anything that can run a command. He can already drive them; this is what
          lets them drive him.
        </p>
      </div>

      {!status.available ? (
        <p className="rounded-md border border-border/60 bg-muted/30 p-3 text-sm text-muted-foreground">
          Not available in this build — the command line ships with the desktop app, and
          this Kith is running as a bare server.
        </p>
      ) : (
        <div className="space-y-4">
          <div className="rounded-md border border-border/60 p-4">
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-1">
                <p className="flex items-center gap-2 text-sm font-medium">
                  {status.installed ? (
                    <>
                      <Check className="size-4 text-emerald-600" />
                      Installed
                    </>
                  ) : status.stale ? (
                    <>
                      <TriangleAlert className="size-4 text-amber-600" />
                      Points at a different Kith
                    </>
                  ) : (
                    "Not installed"
                  )}
                </p>
                <p className="font-mono text-xs text-muted-foreground">{status.link}</p>
              </div>
              <div className="flex shrink-0 gap-2">
                {status.installed ? (
                  <Button variant="outline" size="sm" disabled={working} onClick={() => void act(true)}>
                    {working ? <Loader2 className="size-4 animate-spin" /> : "Remove"}
                  </Button>
                ) : (
                  <Button size="sm" disabled={working} onClick={() => void act(false)}>
                    {working ? <Loader2 className="size-4 animate-spin" /> : status.stale ? "Repair" : "Install"}
                  </Button>
                )}
              </div>
            </div>
            {!status.installed && (
              <p className="mt-3 text-xs text-muted-foreground">
                macOS will ask for your password. It links the command into{" "}
                <code className="font-mono">/usr/local/bin</code>, which is on PATH in every
                terminal — so this is once, not once per update.
              </p>
            )}
            {problem && <p className="mt-3 text-xs text-destructive">{problem}</p>}
          </div>

          {status.installed && (
            <div className="space-y-2 rounded-md border border-border/60 bg-muted/20 p-4">
              <p className="text-sm font-medium">Try it</p>
              <pre className="overflow-x-auto font-mono text-xs text-muted-foreground">
{`kith status                    where am I, is he up
kith send "what's left?"       continue this folder's chat
kith conversations             recent chats
kith --help                    everything else`}
              </pre>
              <p className="text-xs text-muted-foreground">
                Run <code className="font-mono">kith install --claude</code> to tell Claude
                Code that Kith is reachable — without it, a new session has no way to know.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
