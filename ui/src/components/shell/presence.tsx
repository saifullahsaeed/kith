/** The little breathing light that stands in for Kith. Amber when he's awake
 * and with you; green while he's working on his own. */
export function PresenceOrb({
  working = false,
  size = 10,
  idle = false,
}: {
  working?: boolean;
  size?: number;
  idle?: boolean;
}) {
  const orb = working ? "var(--roam)" : "var(--kith)";
  return (
    <span
      className="kith-orb inline-block shrink-0"
      data-idle={idle ? "true" : "false"}
      style={{ width: size, height: size, ["--orb" as string]: orb }}
      aria-hidden
    />
  );
}
