/** The little breathing light that stands in for Kith. Amber when he's awake
 * and with you; green while he's roaming on his own. */
export function PresenceOrb({
  roaming = false,
  size = 10,
  idle = false,
  color,
}: {
  roaming?: boolean;
  size?: number;
  idle?: boolean;
  /** Override the orb colour (e.g. his mood). Ignored while roaming. */
  color?: string;
}) {
  const orb = roaming ? "var(--roam)" : (color ?? "var(--kith)");
  return (
    <span
      className="kith-orb inline-block shrink-0"
      data-idle={idle ? "true" : "false"}
      style={{ width: size, height: size, ["--orb" as string]: orb }}
      aria-hidden
    />
  );
}
