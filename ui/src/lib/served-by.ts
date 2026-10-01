/**
 * Who served a round, as one label: "OpenAI", or "OpenAI · flex" when the host says which of its
 * service tiers took it.
 *
 * The tier is the only record of whether a pin like `openai/flex` held — the pin is a preference
 * with fallbacks on, and a round that fell back to the standard tier is served by the same
 * "OpenAI" at twice the price. "default" is the ordinary tier and says nothing worth showing.
 */
export function servedBy(provider?: string | null, tier?: string | null): string | undefined {
  const host = (provider ?? "").trim();
  if (!host) return undefined;
  const kind = (tier ?? "").trim();
  return kind && kind !== "default" && kind !== "auto" ? `${host} · ${kind}` : host;
}
