import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { PresenceOrb } from "@/components/presence";

/** Full-screen state shown before the server is reachable.
 *
 * The advice differs by how Kith is running, because only one of the two answers is
 * ever right. In the desktop app the server is a child process of the app itself, so
 * telling someone to open a terminal would be nonsense — and in a browser tab during
 * development it is exactly what they need.
 */
export function ConnectionSplash({
  state,
  detail,
}: {
  state: "loading" | "error";
  detail?: string;
}) {
  if (state === "loading") {
    return (
      <Splash title="Waking Kith up…">
        <div className="mt-4 flex justify-center">
          <PresenceOrb size={14} idle />
        </div>
      </Splash>
    );
  }

  const packaged = document.documentElement.hasAttribute("data-desktop");

  return (
    <Splash title="Kith isn't answering" tone="error">
      {packaged ? (
        <p className="text-muted-foreground text-sm leading-relaxed">
          His server didn't start. Quitting and reopening the app usually fixes it.
        </p>
      ) : (
        <>
          <p className="text-muted-foreground text-sm">Start it in a terminal, then retry:</p>
          <pre className="bg-muted my-3 rounded-md px-3 py-2 text-left text-xs">
            cd kith/server{"\n"}.venv/bin/python app.py
          </pre>
        </>
      )}
      {detail ? <p className="text-muted-foreground/70 mt-2 text-xs break-all">{detail}</p> : null}
      <Button className="mt-4" onClick={() => window.location.reload()}>
        Retry
      </Button>
    </Splash>
  );
}

function Splash({
  title,
  tone = "default",
  children,
}: {
  title: string;
  tone?: "default" | "error";
  children?: ReactNode;
}) {
  return (
    <div className="flex h-dvh flex-col items-center justify-center bg-background px-6 text-center text-foreground">
      <h1 className={`text-lg font-semibold ${tone === "error" ? "text-destructive" : ""}`}>
        {title}
      </h1>
      <div className="mt-2 max-w-md">{children}</div>
    </div>
  );
}
