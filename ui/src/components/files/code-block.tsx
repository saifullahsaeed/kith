import { useMemo, useState } from "react";
import { Check, Copy } from "lucide-react";
import { copyText } from "@/lib/files";
import { highlight } from "@/lib/highlight";

/* ── Syntax-highlighted code ────────────────────────────────────────────── */

/** Code with a small toolbar (language + copy). Used for his self-made tools
 * and for fenced blocks inside rendered Markdown. */
export function CodeBlock({
  code,
  language,
  label,
}: {
  code: string;
  language?: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void copyText(code).then((ok) => {
      if (!ok) return;
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  };
  return (
    <div>
      <div className="flex items-center justify-between border-b border-border/50 bg-muted/30 px-3 py-1.5">
        <span className="font-mono text-[11px] lowercase text-muted-foreground">
          {label || language || "text"}
        </span>
        <button
          onClick={copy}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          aria-label="Copy code"
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <Code code={code} language={language} />
    </div>
  );
}

export function Code({
  code,
  language,
  numbered = false,
}: {
  code: string;
  language?: string;
  /** A gutter of line numbers. For a whole file, not for a fenced snippet in chat. */
  numbered?: boolean;
}) {
  const html = useMemo(() => highlight(code, language), [code, language]);
  const numbers = useMemo(
    () => (numbered ? code.split("\n").map((_, index) => index + 1) : []),
    [code, numbered],
  );

  if (!numbered) {
    return (
      <pre className="overflow-x-auto p-4 text-[13px] leading-relaxed">
        <code className="hljs font-mono" dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    );
  }

  // Two columns rather than numbers woven into the markup. The highlighted HTML is one
  // blob whose spans cross line boundaries, so splitting it per line to prefix a number
  // means repairing tags — and getting that subtly wrong colours the rest of the file
  // wrong. A parallel column with the same line-height needs no surgery at all, and it
  // stays put while long lines scroll under it. It holds because the code pre does not
  // wrap: one source line is always exactly one row.
  return (
    <div className="flex min-w-0 items-start font-mono text-[13px] leading-relaxed">
      <pre
        aria-hidden
        className="text-muted-foreground/40 shrink-0 border-r border-border/40 py-4 pr-3 pl-5 text-right tabular-nums select-none"
      >
        {numbers.join("\n")}
      </pre>
      <pre className="min-w-0 flex-1 overflow-x-auto py-4 pr-5 pl-4">
        <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    </div>
  );
}


