import { Cloud, Laptop, Plug, type LucideIcon } from "lucide-react";

import type { ProviderCard, ProviderKind } from "@/lib/backend";

/** One icon per provider. The only presentation the server doesn't supply — an
 *  icon can't travel as JSON, and a lucide name in the API would be worse. */
const ICONS: Record<ProviderKind, LucideIcon> = {
  openrouter: Cloud,
  openai: Plug,
  ollama: Laptop,
};

/** The three ways to give him a brain, as cards.
 *
 * Every word here comes from the server's provider registry, so adding a fourth
 * provider needs no change to this file. The tradeoff line is deliberately the most
 * prominent text after the name: choosing where he thinks is a decision about money
 * and privacy, and the honest version of it belongs on the card, not in a footnote.
 */
export function ProviderChoice({
  providers,
  onChoose,
}: {
  providers: ProviderCard[];
  onChoose: (kind: ProviderKind) => void;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {providers.map((provider, index) => {
        const Icon = ICONS[provider.kind] ?? Cloud;
        return (
          <button
            key={provider.kind}
            type="button"
            onClick={() => onChoose(provider.kind)}
            style={{ animationDelay: `${index * 70}ms` }}
            className="group animate-in fade-in slide-in-from-bottom-2 flex flex-col rounded-xl border bg-card/70 p-4 text-left transition-all duration-200 fill-mode-both hover:-translate-y-0.5 hover:border-kith/50 hover:bg-card hover:shadow-lg focus-visible:border-kith focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            <span className="mb-3 flex size-9 items-center justify-center rounded-lg bg-kith-soft text-kith transition-transform duration-200 group-hover:scale-105">
              <Icon className="size-4.5" />
            </span>
            <span className="text-sm font-semibold">{provider.label}</span>
            <span className="text-muted-foreground mt-0.5 text-xs">{provider.blurb}</span>
            <span className="mt-3 flex-1 text-xs leading-relaxed text-muted-foreground/85">
              {provider.tradeoff}
            </span>
            <span className="mt-3 border-t pt-2.5 text-[11px] text-muted-foreground/70">
              Needs: {provider.requires}
            </span>
          </button>
        );
      })}
    </div>
  );
}
