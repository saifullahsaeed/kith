/** One square icon button, as the viewer's header and its hand-off row both draw it. */
import { useState } from "react";
import type { ReactNode } from "react";
import { Check, Copy } from "lucide-react";

export function IconAction({
  label,
  onClick,
  icon,
  copyIcon,
}: {
  label: string;
  /** For `copyIcon`, a `false` return (or a rejected promise) means the copy didn't actually
   *  happen — the checkmark only shows when this resolves to anything else. */
  onClick: () => void | boolean | Promise<void | boolean>;
  icon?: ReactNode;
  copyIcon?: boolean;
}) {
  const [hit, setHit] = useState(false);
  return (
    <button
      onClick={() => {
        const result = onClick();
        if (!copyIcon) return;
        Promise.resolve(result)
          .then((ok) => {
            if (ok === false) return;
            setHit(true);
            setTimeout(() => setHit(false), 1600);
          })
          .catch(() => {});
      }}
      title={label}
      aria-label={label}
      className="flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/25 focus-visible:outline-none"
    >
      {copyIcon ? hit ? <Check className="size-4 text-kith" /> : <Copy className="size-4" /> : icon}
    </button>
  );
}
