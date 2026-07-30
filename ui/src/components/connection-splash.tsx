import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

/** Full-screen state shown before the server is reachable. */
export function ConnectionSplash({ state, detail }: { state: "loading" | "error"; detail?: string }) {
  if (state === "loading") {
    return <Splash title="Connecting to Kith…" />;
  }

  return (
    <Splash title="Can't reach the Kith server" tone="error">
      <p className="text-muted-foreground text-sm">Start it in a terminal, then retry:</p>
      <pre className="bg-muted my-3 rounded-md px-3 py-2 text-left text-xs">
        cd kith/server{"\n"}.venv/bin/python app.py
      </pre>
      {detail ? <p className="text-muted-foreground/70 text-xs break-all">{detail}</p> : null}
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
