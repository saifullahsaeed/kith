"use client";

import { memo, useCallback, useRef, useState } from "react";
import {
  AlertCircleIcon,
  BanIcon,
  ChevronDownIcon,
  LoaderIcon,
  ShieldXIcon,
  SquareIcon,
  XCircleIcon,
} from "lucide-react";
import {
  useAuiState,
  useScrollLock,
  useToolCallElapsed,
  type ToolApprovalOption,
  type ToolCallMessagePart,
  type ToolCallMessagePartProps,
  type ToolCallMessagePartStatus,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import { ANIMATION_DURATION } from "./disclosure";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { callText, describeCall } from "@/lib/tool-language";
import { digestOf, outcomeOf, type Outcome } from "@/lib/tool-row";
import { useProjectRoot } from "@/lib/project-root";
import { ToolArgs, ToolResultBody, parseArgs } from "@/components/assistant-ui/tool-result";
import { Button } from "@/components/ui/button";


const pressable = "active:scale-[0.98]";

export type ToolFallbackRootProps = Omit<
  React.ComponentProps<typeof Collapsible>,
  "open" | "onOpenChange"
> & {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  defaultOpen?: boolean;
};

function ToolFallbackRoot({
  className,
  open: controlledOpen,
  onOpenChange: controlledOnOpenChange,
  defaultOpen = false,
  children,
  ...props
}: ToolFallbackRootProps) {
  const collapsibleRef = useRef<HTMLDivElement>(null);
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const lockScroll = useScrollLock(collapsibleRef, ANIMATION_DURATION);

  const isControlled = controlledOpen !== undefined;
  const isOpen = isControlled ? controlledOpen : uncontrolledOpen;

  const handleOpenChange = useCallback(
    (open: boolean) => {
      lockScroll();
      if (!isControlled) {
        setUncontrolledOpen(open);
      }
      controlledOnOpenChange?.(open);
    },
    [lockScroll, isControlled, controlledOnOpenChange],
  );

  return (
    <Collapsible
      ref={collapsibleRef}
      data-slot="tool-fallback-root"
      open={isOpen}
      onOpenChange={handleOpenChange}
      className={cn("aui-tool-fallback-root group/tool-fallback-root w-full", className)}
      style={
        {
          "--animation-duration": `${ANIMATION_DURATION}ms`,
        } as React.CSSProperties
      }
      {...props}
    >
      {children}
    </Collapsible>
  );
}

const formatToolDuration = (ms: number) => {
  if (ms < 1000) return "<1s";
  const seconds = ms / 1000;
  if (seconds < 10) return `${(Math.floor(seconds * 10) / 10).toFixed(1)}s`;
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
};

function ToolFallbackDuration({ className, ...props }: React.ComponentProps<"span">) {
  const elapsedMs = useToolCallElapsed();
  if (elapsedMs === undefined) return null;

  return (
    <span
      data-slot="tool-fallback-duration"
      className={cn(
        "aui-tool-fallback-duration text-muted-foreground text-xs tabular-nums",
        className,
      )}
      {...props}
    >
      {formatToolDuration(elapsedMs)}
    </span>
  );
}

/**
 * The row for one tool call, in the grammar every row in the thread shares:
 *
 *     [kind] Verb subject ·························· digest  elapsed  ›
 *            the first line of what went wrong, when something did
 *
 * **Kind** is the icon of the work (a terminal, a file, a globe), and a state worth interrupting a
 * scan for takes its slot: a spinner while it runs, a red cross when it failed, a shield when a
 * permission was refused, a ban when one of his own rules stopped it. Nearly every call succeeds,
 * so a tick on every row carried nothing and is not drawn.
 *
 * **Digest** is the one fact about what came back — `exit 2 · 14 lines`, `+12 −3`, `3 errors` —
 * right-aligned so the subject can truncate without taking it with it. It used to be glued on
 * after the subject and repeat it ("ran npm test · npm test · ok"), so on a narrow pane the row
 * cut off exactly the part that was new.
 *
 * **The reason line** is new, and is the difference between a turn you can skim and one you have
 * to open: a failure said "failed" and made you open the row to learn why. The why is one line,
 * so it is shown.
 */
function ToolFallbackTrigger({
  toolName,
  args,
  status,
  outcome,
  digest,
  recovered,
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger> & {
  toolName: string;
  /** The call's parsed arguments, for naming what it acted on. */
  args?: Record<string, unknown>;
  status?: ToolCallMessagePartStatus;
  /** How it ended, once it has — `undefined` while it is still going. */
  outcome?: Outcome;
  /** One fact about what came back. */
  digest?: string;
  /** It worked after the call before it, of the same tool, did not. */
  recovered?: boolean;
}) {
  const root = useProjectRoot();
  const statusType = status?.type ?? "complete";
  const isRunning = statusType === "running";
  const isCancelled = status?.type === "incomplete" && status.reason === "cancelled";
  const isErrored = statusType === "incomplete" && !isCancelled;
  const needsAction = statusType === "requires-action";
  const state = isErrored ? "failed" : (outcome?.state ?? "done");

  const call = describeCall(toolName, args, root);
  const verb = isRunning ? call.doing : call.verb;
  // One definition of the line, used both as the label and as the shimmer laid over it.
  const line = (
    <>
      {verb.charAt(0).toUpperCase() + verb.slice(1)}
      {call.subject ? (
        <>
          {" "}
          {/* Mono for the subject: a path, a command or a query is scannable that way, and
              obviously a literal rather than part of the sentence. */}
          <span className="text-foreground/85 font-mono">{call.subject}</span>
        </>
      ) : null}
    </>
  );

  // The kind icon is the resting state; a state that is worth noticing takes the slot.
  const Icon = isRunning
    ? LoaderIcon
    : needsAction
      ? AlertCircleIcon
      : isCancelled
        ? SquareIcon
        : state === "failed"
          ? XCircleIcon
          : state === "denied"
            ? ShieldXIcon
            : state === "refused"
              ? BanIcon
              : call.icon;
  const tone = isRunning
    ? "text-roam"
    : needsAction || state === "refused"
      ? "text-kith"
      : state === "failed" || state === "denied"
        ? "text-destructive"
        : isCancelled
          ? "text-muted-foreground/70"
          : call.tone;

  const said =
    isCancelled
      ? "stopped"
      : state === "failed"
        ? "failed"
        : state === "denied"
          ? "not allowed"
          : state === "refused"
            ? "a rule stopped this"
            : digest;
  const reason = state !== "done" ? (outcome?.reason ?? "") : "";

  return (
    <CollapsibleTrigger
      data-slot="tool-fallback-trigger"
      data-state-kind={isRunning ? "running" : isCancelled ? "stopped" : state}
      title={callText(call)}
      className={cn(
        "aui-tool-fallback-trigger group/trigger text-muted-foreground hover:text-foreground flex w-full min-w-0 origin-left flex-col py-1 text-start text-xs transition-[color,scale] active:scale-[0.98]",
        className,
      )}
      {...props}
    >
      <span className="flex w-full min-w-0 items-center gap-2">
        <Icon
          data-slot="tool-fallback-trigger-icon"
          strokeWidth={2}
          className={cn(
            "aui-tool-fallback-trigger-icon size-3.5 shrink-0",
            tone,
            isRunning && "animate-spin [animation-duration:0.6s]",
          )}
        />
        <span
          data-slot="tool-fallback-trigger-label"
          className={cn(
            // One line, ellipsised. A path or a command that wraps turns a run of eight calls
            // into a wall you have to read rather than a list you can scan; the whole value is
            // on the row's title and inside the disclosure.
            "aui-tool-fallback-trigger-label-wrapper relative min-w-0 flex-1 truncate leading-normal",
            isRunning && "text-roam",
            isCancelled && "line-through",
          )}
        >
          <span>{line}</span>
          {/* The shimmer is the same line again, drawn on top with its glyphs used as a mask.
              It renders `line` — the identical element — so the two cannot drift apart: an
              earlier version rendered a flat string, set the subject in the body font over a
              mono line, and ghosted every running tool's name until it returned. */}
          {isRunning && (
            <span
              aria-hidden
              data-slot="tool-fallback-trigger-shimmer"
              className="aui-tool-fallback-trigger-shimmer shimmer pointer-events-none absolute inset-0 truncate motion-reduce:animate-none"
            >
              {line}
            </span>
          )}
        </span>
        {call.badge ? (
          <span className="bg-muted text-muted-foreground shrink-0 rounded px-1.5 py-px text-[10px] font-medium">
            {call.badge}
          </span>
        ) : null}
        {outcome?.ranAs ? (
          <span
            className="bg-muted text-muted-foreground/80 shrink-0 rounded px-1.5 py-px font-mono text-[10px]"
            title={`${toolName} is a retired name — this ran as ${outcome.ranAs}`}
          >
            ran as {outcome.ranAs}
          </span>
        ) : null}
        {recovered ? (
          <span className="bg-roam-soft text-roam shrink-0 rounded px-1.5 py-px text-[10px] font-medium">
            recovered
          </span>
        ) : null}
        {said ? (
          <span
            data-slot="tool-fallback-trigger-digest"
            className={cn(
              "max-w-[45%] shrink-0 truncate tabular-nums",
              state === "failed" || state === "denied"
                ? "text-destructive/90"
                : state === "refused"
                  ? "text-kith/90"
                  : "text-muted-foreground/70",
            )}
          >
            {said}
          </span>
        ) : null}
        <ToolFallbackDuration />
        <ChevronDownIcon
          data-slot="tool-fallback-trigger-chevron"
          className={cn(
            "aui-tool-fallback-trigger-chevron size-3.5 shrink-0 opacity-60",
            "transition-transform duration-(--animation-duration) ease-[cubic-bezier(0.32,0.72,0,1)] motion-reduce:transition-none",
            "-rotate-90",
            "group-data-open/trigger:rotate-0",
            "group-data-panel-open/trigger:rotate-0",
          )}
        />
      </span>
      {reason ? (
        <span
          data-slot="tool-fallback-trigger-reason"
          className={cn(
            "block w-full truncate ps-5.5 pt-0.5 text-[11.5px] leading-snug",
            state === "refused" ? "text-kith/80" : "text-destructive/80",
          )}
        >
          {reason}
        </span>
      ) : null}
    </CollapsibleTrigger>
  );
}

function ToolFallbackContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof CollapsibleContent>) {
  return (
    <CollapsibleContent
      data-slot="tool-fallback-content"
      className={cn(
        "aui-tool-fallback-content relative overflow-hidden text-sm outline-none",
        "group/collapsible-content ease-[cubic-bezier(0.32,0.72,0,1)] motion-reduce:animate-none",
        "data-closed:animate-collapsible-up",
        "data-open:animate-collapsible-down",
        "data-closed:fill-mode-forwards",
        "data-closed:pointer-events-none",
        "data-open:duration-(--animation-duration)",
        "data-closed:duration-(--animation-duration)",
        className,
      )}
      {...props}
    >
      <div
        className={cn(
          "flex flex-col gap-2 ps-6 pt-1 pb-2 ease-[cubic-bezier(0.32,0.72,0,1)] motion-reduce:animate-none",
          "group-data-open/collapsible-content:animate-in group-data-open/collapsible-content:fade-in-0 group-data-open/collapsible-content:blur-in-[2px] group-data-open/collapsible-content:slide-in-from-top-1",
          "group-data-closed/collapsible-content:animate-out group-data-closed/collapsible-content:fade-out-0 group-data-closed/collapsible-content:blur-out-[2px] group-data-closed/collapsible-content:slide-out-to-top-1",
          "group-data-closed/collapsible-content:duration-(--animation-duration) group-data-open/collapsible-content:duration-(--animation-duration)",
        )}
      >
        {children}
      </div>
    </CollapsibleContent>
  );
}

function ToolFallbackArgs({
  argsText,
  toolName,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  argsText?: string;
  toolName?: string;
}) {
  if (!argsText) return null;

  return (
    <div
      data-slot="tool-fallback-args"
      className={cn("aui-tool-fallback-args", className)}
      {...props}
    >
      {/* Labelled rows, not a JSON blob. The arguments are the half that made today's
          diagnoses possible — seeing `after: [35]` go in and nothing come out — so they are
          worth reading rather than decoding. */}
      <ToolArgs argsText={argsText} toolName={toolName} />
    </div>
  );
}

function ToolFallbackResult({
  result,
  toolName,
  argsText,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  result?: unknown;
  toolName?: string;
  argsText?: string;
}) {
  if (result === undefined) return null;

  return (
    <div
      data-slot="tool-fallback-result"
      className={cn("aui-tool-fallback-result", className)}
      {...props}
    >
      <ToolResultBody name={toolName ?? ""} args={parseArgs(argsText)} result={result} />
    </div>
  );
}

function ToolFallbackError({
  status,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  status?: ToolCallMessagePartStatus;
}) {
  if (status?.type !== "incomplete") return null;

  const error = status.error;
  const errorText = error ? (typeof error === "string" ? error : JSON.stringify(error)) : null;

  if (!errorText) return null;

  const isCancelled = status.reason === "cancelled";
  const headerText = isCancelled ? "Cancelled reason:" : "Error:";

  return (
    <div
      data-slot="tool-fallback-error"
      className={cn("aui-tool-fallback-error", className)}
      {...props}
    >
      <p className="aui-tool-fallback-error-header text-muted-foreground font-semibold">
        {headerText}
      </p>
      <p className="aui-tool-fallback-error-reason text-muted-foreground">{errorText}</p>
    </div>
  );
}

const APPROVED_RESULT = "Approved by user";
const DENIED_RESULT = "User denied tool execution";

const APPROVAL_OPTION_DEFAULT_LABELS: Record<string, string> = {
  "allow-once": "Allow",
  "allow-always": "Always allow",
  "reject-once": "Deny",
  "reject-always": "Always deny",
};

const isAllowKind = (kind: string) => kind === "allow-once" || kind === "allow-always";

const approvalOptionLabel = (option: ToolApprovalOption) =>
  option.label ??
  (Object.hasOwn(APPROVAL_OPTION_DEFAULT_LABELS, option.kind)
    ? APPROVAL_OPTION_DEFAULT_LABELS[option.kind]
    : undefined) ??
  option.id;

function ToolFallbackApproval({
  className,
  addResult,
  resume,
  interrupt,
  approval,
  respondToApproval,
  ...props
}: React.ComponentProps<"div"> &
  Partial<Pick<ToolCallMessagePartProps, "addResult" | "resume" | "respondToApproval">> & {
    interrupt?: ToolCallMessagePart["interrupt"];
    approval?: ToolCallMessagePart["approval"];
  }) {
  const [submitted, setSubmitted] = useState(false);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  if (approval != null && (approval.approved !== undefined || approval.resolution !== undefined))
    return null;

  // Custom (`_`-prefixed) kinds cannot be resolved to a boolean by the kit;
  // hosts using custom kinds render their own bar. A declared option list is
  // a host constraint: the kit never adds an approval path beyond it, but
  // always preserves a refusal path.
  const declaredOptions = respondToApproval ? approval?.options : undefined;
  const options = declaredOptions?.filter((o) =>
    Object.hasOwn(APPROVAL_OPTION_DEFAULT_LABELS, o.kind),
  );

  const respond = (approved: boolean) => {
    if (submitted) return;
    if (approval != null && approval.approved === undefined && respondToApproval) {
      respondToApproval({ approved });
    } else if (interrupt) {
      resume?.({ approved });
    } else {
      addResult?.(approved ? APPROVED_RESULT : DENIED_RESULT);
    }
    setSubmitted(true);
  };

  const respondWithOption = (option: ToolApprovalOption) => {
    if (submitted) return;
    respondToApproval?.({ optionId: option.id });
    setSubmitted(true);
    setConfirmingId(null);
  };

  const handleOption = (option: ToolApprovalOption) => {
    if (option.confirm) {
      setConfirmingId(option.id);
    } else {
      respondWithOption(option);
    }
  };

  const confirming = confirmingId != null ? options?.find((o) => o.id === confirmingId) : undefined;

  if (confirming) {
    const confirmMeta = typeof confirming.confirm === "object" ? confirming.confirm : undefined;
    const confirmDescription = confirmMeta?.description ?? confirming.description;
    return (
      <div
        data-slot="tool-fallback-approval-confirm"
        className={cn("aui-tool-fallback-approval-confirm flex flex-col gap-2 pt-1", className)}
        {...props}
      >
        <p className="aui-tool-fallback-approval-confirm-title font-semibold">
          {confirmMeta?.title ?? `${approvalOptionLabel(confirming)}?`}
        </p>
        {confirmDescription && (
          <p className="aui-tool-fallback-approval-confirm-description text-muted-foreground">
            {confirmDescription}
          </p>
        )}
        {confirming.grants && confirming.grants.length > 0 && (
          <ul className="aui-tool-fallback-approval-confirm-grants flex flex-col gap-1">
            {confirming.grants.map((grant) => (
              <li key={grant}>
                <code className="aui-tool-fallback-approval-confirm-grant bg-muted rounded px-1.5 py-0.5 text-xs">
                  {grant}
                </code>
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            className={pressable}
            onClick={() => respondWithOption(confirming)}
            disabled={submitted}
          >
            Confirm
          </Button>
          <Button
            size="sm"
            variant="outline"
            className={pressable}
            onClick={() => setConfirmingId(null)}
            disabled={submitted}
          >
            Back
          </Button>
        </div>
      </div>
    );
  }

  if (declaredOptions && declaredOptions.length > 0) {
    const allowOptions = options?.filter((o) => isAllowKind(o.kind)) ?? [];
    const rejectOptions = options?.filter((o) => !isAllowKind(o.kind)) ?? [];
    return (
      <div
        data-slot="tool-fallback-approval"
        className={cn(
          "aui-tool-fallback-approval flex flex-wrap items-center gap-2 pt-1",
          className,
        )}
        {...props}
      >
        {[...allowOptions, ...rejectOptions].map((option) => (
          <Button
            key={option.id}
            size="sm"
            variant={option === allowOptions[0] ? "default" : "outline"}
            className={pressable}
            onClick={() => handleOption(option)}
            disabled={submitted}
          >
            {approvalOptionLabel(option)}
          </Button>
        ))}
        {rejectOptions.length === 0 && (
          <Button
            size="sm"
            variant="outline"
            className={pressable}
            onClick={() => respond(false)}
            disabled={submitted}
          >
            Deny
          </Button>
        )}
      </div>
    );
  }

  return (
    <div
      data-slot="tool-fallback-approval"
      className={cn("aui-tool-fallback-approval flex items-center gap-2 pt-1", className)}
      {...props}
    >
      <Button size="sm" className={pressable} onClick={() => respond(true)} disabled={submitted}>
        Allow
      </Button>
      <Button
        size="sm"
        variant="outline"
        className={pressable}
        onClick={() => respond(false)}
        disabled={submitted}
      >
        Deny
      </Button>
    </div>
  );
}

const ToolFallbackImpl: ToolCallMessagePartComponent = ({
  toolCallId,
  toolName,
  argsText,
  result,
  status,
  addResult,
  resume,
  interrupt,
  approval,
  respondToApproval,
}) => {
  const isCancelled = status?.type === "incomplete" && status.reason === "cancelled";
  const isRequiresAction = status?.type === "requires-action";

  const [open, setOpen] = useState(isRequiresAction);
  const [prevRequiresAction, setPrevRequiresAction] = useState(isRequiresAction);
  if (isRequiresAction !== prevRequiresAction) {
    setPrevRequiresAction(isRequiresAction);
    if (isRequiresAction) setOpen(true);
  }

  const args = parseArgs(argsText);
  const finished = result !== undefined && !isRequiresAction;
  const outcome = finished ? outcomeOf(toolName ?? "", result) : undefined;

  /* Recovered: this call worked, and the last call of the same tool before it in this message did
   * not. A failure followed by its own fix is routine — he tried a flag the command did not take,
   * then the right one — and without saying so a turn with four red rows reads as four problems
   * when it had none left. A boolean out of the selector, so rows re-render only when it flips. */
  const recovered = useAuiState((state) => {
    if (outcome?.state !== "done") return false;
    const parts = state.message.parts;
    const at = parts.findIndex((part) => part.type === "tool-call" && part.toolCallId === toolCallId);
    for (let i = at - 1; i >= 0; i--) {
      const part = parts[i];
      if (part?.type !== "tool-call" || part.toolName !== toolName) continue;
      return part.result !== undefined && outcomeOf(part.toolName, part.result).state === "failed";
    }
    return false;
  });

  return (
    <ToolFallbackRoot open={open} onOpenChange={setOpen}>
      <ToolFallbackTrigger
        toolName={toolName}
        args={args}
        status={status}
        outcome={outcome}
        digest={finished ? digestOf(toolName ?? "", args, result) : undefined}
        recovered={recovered}
      />
      <ToolFallbackContent>
        <ToolFallbackError status={status} />
        <ToolFallbackArgs
          argsText={argsText}
          toolName={toolName}
          className={cn(isCancelled && "opacity-60")}
        />
        {isRequiresAction && (
          <ToolFallbackApproval
            addResult={addResult}
            resume={resume}
            interrupt={interrupt}
            approval={approval}
            respondToApproval={respondToApproval}
          />
        )}
        {!isCancelled && (
          <ToolFallbackResult result={result} toolName={toolName} argsText={argsText} />
        )}
      </ToolFallbackContent>
    </ToolFallbackRoot>
  );
};

const ToolFallback = memo(ToolFallbackImpl) as unknown as ToolCallMessagePartComponent & {
  Root: typeof ToolFallbackRoot;
  Trigger: typeof ToolFallbackTrigger;
  Content: typeof ToolFallbackContent;
  Args: typeof ToolFallbackArgs;
  Result: typeof ToolFallbackResult;
  Error: typeof ToolFallbackError;
  Approval: typeof ToolFallbackApproval;
};

ToolFallback.displayName = "ToolFallback";
ToolFallback.Root = ToolFallbackRoot;
ToolFallback.Trigger = ToolFallbackTrigger;
ToolFallback.Content = ToolFallbackContent;
ToolFallback.Args = ToolFallbackArgs;
ToolFallback.Result = ToolFallbackResult;
ToolFallback.Error = ToolFallbackError;
ToolFallback.Approval = ToolFallbackApproval;

export {
  ToolFallback,
  ToolFallbackRoot,
  ToolFallbackTrigger,
  ToolFallbackContent,
  ToolFallbackArgs,
  ToolFallbackResult,
  ToolFallbackError,
  ToolFallbackApproval,
};
