import { useQuery } from "@tanstack/react-query";
import { ArrowUpCircle, Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { checkForUpdate, fetchUpdate, type UpdateState } from "@/lib/backend";
import { keys } from "@/lib/query-keys";

/**
 * There is a newer Kith than this one.
 *
 * Nothing in a running Kith mentioned that releases exist, so a copy installed in August stayed
 * on that version — not by choice, but because there was no way to find out. The check lives on
 * the server (`services/updates.py`) rather than here, because the menu bar reads the same
 * answer and two implementations would eventually disagree about what "newer" means.
 *
 * **Told, not installed.** macOS auto-update verifies the downloaded app's signature against the
 * running one, and Kith is ad-hoc signed, so an in-place swap is impossible without a paid
 * Developer ID. Every button here therefore ends at the download rather than pretending to do
 * something it cannot.
 */

/** Shared by both surfaces so a click in one is a fetch neither repeats. */
function useUpdate() {
  return useQuery({
    queryKey: keys.update(),
    queryFn: fetchUpdate,
    // The server caches for six hours; this is only about not re-asking it on every mount.
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/**
 * The visible one: a pill in the app header, present for as long as the update is.
 *
 * Not a banner and not dismissible. A banner covers what you were reading and a dismissible
 * notice is one you dismiss and never see again — which is how you end up three versions behind
 * having been told once, months ago. This takes a corner of a bar that is already there, says
 * the version, and waits.
 */
export function UpdatePill({ onOpen }: { onOpen: () => void }) {
  const { data } = useUpdate();
  if (!data?.newer) return null;

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={onOpen}
      title={`Kith ${data.latest} is out — you are on ${data.current}`}
      className="bg-kith-soft text-kith hover:bg-kith-soft hover:text-kith ring-kith/20 h-7 gap-1.5 rounded-full px-2.5 text-[11px] ring-1"
    >
      <ArrowUpCircle className="size-3.5" />
      {data.latest}
    </Button>
  );
}

/**
 * The detailed one: a footer under the settings nav, on every tab.
 *
 * Under the nav rather than inside a tab because it is about the application rather than about
 * anything it is configured to do — and because a seventh tab holding one paragraph would be a
 * worse answer than a line that is simply always there. It also means the running version is
 * visible whenever settings are open, which is the thing you want when writing a bug report.
 */
export function UpdateFooter() {
  const { data, refetch, isFetching } = useUpdate();

  const look = async () => {
    await checkForUpdate().catch(() => undefined);
    await refetch();
  };

  return (
    <div className="border-border/60 mt-4 border-t px-2.5 pt-3">
      {data?.newer ? (
        <a
          href={data.download || data.page}
          target="_blank"
          rel="noreferrer"
          className="bg-kith-soft text-kith ring-kith/20 hover:bg-kith-soft/80 flex items-center gap-2 rounded-lg px-2.5 py-2 ring-1 transition-colors"
        >
          <ArrowUpCircle className="size-4 shrink-0" />
          <span className="min-w-0 flex-1">
            <span className="block text-[12px] font-medium">Kith {data.latest} is out</span>
            <span className="block text-[10px] opacity-70">
              {data.download ? "Download the disk image" : "Open the release"}
            </span>
          </span>
        </a>
      ) : null}

      <div className="text-muted-foreground/60 mt-2 flex items-baseline gap-2 px-0.5 text-[10px]">
        <span className="font-mono tabular-nums">{version(data)}</span>
        <button
          type="button"
          onClick={() => void look()}
          disabled={isFetching}
          className="hover:text-foreground ms-auto flex items-center gap-1 transition-colors disabled:opacity-50"
        >
          {isFetching ? (
            <Loader2 className="size-2.5 animate-spin" />
          ) : (
            <RefreshCw className="size-2.5" />
          )}
          Check
        </button>
      </div>

      {/* The failure that must not look like success. "Could not reach GitHub" and "you are up
          to date" are different facts, and a screen that renders them identically will
          eventually tell someone they are current when nothing has looked in a month. */}
      {data?.error ? (
        <p className="text-muted-foreground/50 mt-1 px-0.5 text-[10px] leading-snug">
          Could not check for updates.
        </p>
      ) : null}
    </div>
  );
}

/** What the footer says about the copy you are running. Exported so the rule is testable
 *  without mounting anything. */
export function version(data: UpdateState | undefined): string {
  if (!data) return "";
  if (!data.current) return data.packaged ? "" : "running from source";
  // Which version, and then what is known about it. This said only "running from source" for a
  // checkout, which is the wrong half: where a copy came from is rarely the question, and the
  // number is the first thing anyone is asked for when they report something.
  const running = `Kith ${data.current}`;
  // No claim about being current for a checkout — a working tree is not a thing that gets
  // updated, and telling a developer they are behind their own source is noise.
  if (!data.packaged) return `${running} · from source`;
  if (data.newer) return running;
  // "latest" is a claim, and a look that failed with nothing remembered has not earned it. The
  // footer says the check failed underneath; the version line must not contradict it.
  if (!data.latest) return running;
  return `${running} · latest`;
}

