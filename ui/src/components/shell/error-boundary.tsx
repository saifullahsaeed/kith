import { Component, type ErrorInfo, type ReactNode } from "react";
import { RotateCw, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { copyText } from "@/lib/files";

/**
 * What you see when part of the interface throws.
 *
 * Before this there was nothing, and React's default for an uncaught render error is to
 * unmount the entire tree — so one bad value anywhere produced a white window with no
 * message, no way back, and nothing in the interface saying what had happened. On a desktop
 * app whose whole job is to be sitting there when you need it, that is the worst failure mode
 * available: it looks identical to the app being broken beyond repair, and the only recovery
 * anyone would guess at is quitting.
 *
 * Three levels of it, deliberately. One around the whole app so a crash is a message rather
 * than a blank screen, one around each panel — Mind, the Control Panel, Settings, the
 * thread — so a panel that fails takes only itself down, and one around each *fence in a
 * reply*. The chat surviving a broken roadmap graph is the difference between "one thing is
 * wrong" and "Kith is down"; the thread surviving a diagram is the difference between "that
 * diagram did not draw" and losing the conversation you were reading.
 *
 * That third level is what `fallback` is for. A drawing that fails has an obvious right answer
 * — the source, which is what the fence would have shown before anything drew it — and a
 * centred alert with a "Try again" button in the middle of a paragraph is not it. Given a
 * fallback, this boundary shows that instead of saying anything itself.
 *
 * `where` names the part that failed, because "something went wrong" is not a bug report and
 * the person reading it is the one who has to decide whether to keep working.
 */
export class ErrorBoundary extends Component<
  {
    children: ReactNode;
    where: string;
    compact?: boolean;
    /** Shown in place of everything below, when the thing that failed is small enough that a
     *  quieter answer is the better one. */
    fallback?: ReactNode;
  },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Kept on the console as well as on screen: the stack is what makes it fixable, and the
    // panel has room for a sentence rather than a stack trace.
    console.error(`[kith] ${this.props.where} crashed`, error, info.componentStack);
  }

  private retry = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    // Small enough to answer quietly. The console still has the stack — see `componentDidCatch`
    // — so a fence that silently falls back to its source is still a reported crash.
    if (this.props.fallback !== undefined) return <>{this.props.fallback}</>;

    const details = `${this.props.where}: ${error.message}\n\n${error.stack ?? ""}`;

    return (
      <div
        role="alert"
        className={
          this.props.compact
            ? "flex h-full flex-col items-center justify-center gap-3 p-6 text-center"
            : "flex h-full flex-col items-center justify-center gap-4 p-10 text-center"
        }
      >
        <span className="bg-destructive/10 text-destructive flex size-11 items-center justify-center rounded-full">
          <TriangleAlert className="size-5" />
        </span>
        <div className="space-y-1">
          <p className="text-sm font-medium">{this.props.where} stopped working.</p>
          {/* The actual message, not a euphemism. Someone who can read "undefined is not a
              function" can tell you what they were doing when it happened. */}
          <p className="text-muted-foreground max-w-sm font-mono text-[11px] break-words">
            {error.message || "No message was given."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={this.retry}>
            <RotateCw className="size-3.5" />
            Try again
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="text-muted-foreground hover:text-foreground"
            onClick={() => void copyText(details)}
          >
            Copy the details
          </Button>
        </div>
        {/* Only for the whole-app case. Inside a panel, the rest of the app is still working
            and reloading would throw away whatever is in the composer. */}
        {!this.props.compact ? (
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="text-muted-foreground/60 hover:text-foreground text-[11px] underline-offset-2 hover:underline"
          >
            Reload the window
          </button>
        ) : null}
      </div>
    );
  }
}
