/**
 * The design system a screen is drawn in — which is not, and never should have been, Kith's own.
 *
 * ── The mistake this corrects ───────────────────────────────────────────────────────────────
 *
 * The dialect said "colour is a token, never a literal", and then hardcoded which nine tokens
 * existed: `--kith-accent` and its eight siblings, the app's own amber-on-warm-dark. That rule is
 * right for a canvas drawn *inside* Kith and wrong for the thing this surface is actually for —
 * designing somebody's product. Every screen came out looking like Kith, and the only escape the
 * format offered was a literal hex, which the skill forbids and the checker fails. So the
 * constraint that was supposed to buy consistency bought uniformity instead, and there was no
 * legal way out of it.
 *
 * A system is a document now. Kith's nine are one instance of it rather than the shape of the
 * world.
 *
 * ── Two shapes in, one shape out ────────────────────────────────────────────────────────────
 *
 * `design-system.json` is the written form. But the owner's system already existed before this
 * surface did — `brand/profile.json` in the sadeef-design-engine skill, with 21 colours, nine
 * font roles, a twelve-role type scale in points, and five radii. Asking for it to be
 * re-authored in a new format would be asking for two files that disagree by next month, so the
 * adapter reads it directly.
 *
 * Points become pixels by *ratio*, not by the deck's own `px_per_pt`. A deck is 1920 wide and a
 * screen is not, so honouring the print calibration would produce type sized for a projector.
 * Body is the anchor — 12pt is 16px, everything scales from that — which lands hero at 57px and
 * eyebrow at 9px. The result is marked `derived` so the inspector can say where the numbers came
 * from rather than implying he chose them.
 */

export interface TextRole {
  size: string;
  weight?: number;
  /** `upper` becomes text-transform; anything else is left alone. */
  transform?: string;
  tracking?: string;
  /** Which font role this text uses. */
  font?: string;
}

/** One named part of the project's inventory: markup written once and composed with. */
export interface Component {
  name: string;
  html: string;
  /** When to reach for it, in one line. */
  use: string;
}

export interface System {
  name: string;
  /** Token name to CSS colour. Emitted as `--ds-<name>`. */
  colors: Record<string, string>;
  /** Named text roles. Emitted as `--ds-text-<role>-size` and `-weight`. */
  text: Record<string, TextRole>;
  /** The spacing scale, ascending. The checker measures against this rather than a fixed 4. */
  space: number[];
  radius: Record<string, string>;
  /** Font role to family stack. Emitted as `--ds-font-<role>`. */
  fonts: Record<string, string>;
  /** True when the numbers were converted from another format rather than authored. */
  derived?: boolean;
  /**
   * The parts this product is built out of. Empty for a system that has not grown one yet,
   * which is every system on its first day.
   */
  components: Record<string, Component>;
  /**
   * How screens are built for this product, in prose. Lives in a markdown file beside the
   * system and is resolved with it, so a project cannot end up with its conventions attached
   * to somebody else's palette.
   */
  conventions: string;
}

/**
 * The inventory, validated.
 *
 * A component is markup that will be rendered into a design, so the same thinking that governs
 * `parse.ts` governs what may be stored. Mirrors `_components` in
 * `server/kith/services/design_system.py`; if the two refuse different things, a part the
 * server accepted is a part the editor silently will not draw.
 */
function components(raw: unknown): Record<string, Component> {
  const out: Record<string, Component> = {};
  if (!raw || typeof raw !== "object") return out;
  for (const [name, spec] of Object.entries(raw as Record<string, unknown>)) {
    if (!/^[\w.-]{1,60}$/.test(name)) continue;
    const entry = (typeof spec === "string" ? { html: spec } : spec) as Record<string, unknown>;
    const html = String(entry?.html ?? "").trim();
    if (!html || html.length > 4000) continue;
    if (/<\s*script|javascript:|\son\w+\s*=|src\s*=\s*['"]?https?:/i.test(html)) continue;
    out[name] = { name, html, use: String(entry?.use ?? "").trim().slice(0, 200) };
  }
  return out;
}

/** A colour as written in a profile: bare hex, `#hex`, or any CSS colour. */
function colour(raw: unknown): string | null {
  const text = String(raw ?? "").trim();
  if (!text) return null;
  if (/^[0-9a-f]{3,8}$/i.test(text)) return `#${text}`;
  // Anything else is passed through and validated downstream by the same rules as a design's own
  // values — a system file is a file in his workspace, not a trusted source.
  if (/^[\w#(),.%\s/-]{1,80}$/.test(text)) return text;
  return null;
}

function px(value: unknown): string | null {
  const n = Number(value);
  if (Number.isFinite(n)) return `${Math.round(n)}px`;
  const text = String(value ?? "").trim();
  return /^[\d.]+(px|rem|em|%)$/.test(text) ? text : null;
}

/** The written form: a design-system.json authored for this surface. */
function fromDesignSystem(doc: Record<string, unknown>): System | null {
  const colorsIn = doc.colors;
  if (!colorsIn || typeof colorsIn !== "object") return null;

  const colors: Record<string, string> = {};
  for (const [name, value] of Object.entries(colorsIn as Record<string, unknown>)) {
    const hex = colour(value);
    if (hex && /^[\w-]{1,40}$/.test(name)) colors[name] = hex;
  }
  if (!Object.keys(colors).length) return null;

  const text: Record<string, TextRole> = {};
  for (const [role, value] of Object.entries((doc.text ?? {}) as Record<string, unknown>)) {
    const spec = value as Record<string, unknown>;
    const size = px(spec?.size);
    if (!size || !/^[\w-]{1,40}$/.test(role)) continue;
    text[role] = {
      size,
      weight: Number.isFinite(Number(spec.weight)) ? Number(spec.weight) : undefined,
      transform: spec.case === "upper" ? "uppercase" : undefined,
      tracking: typeof spec.tracking === "string" ? spec.tracking : undefined,
      font: typeof spec.font === "string" ? spec.font : undefined,
    };
  }

  const radius: Record<string, string> = {};
  for (const [name, value] of Object.entries((doc.radius ?? {}) as Record<string, unknown>)) {
    const size = px(value);
    if (size && /^[\w-]{1,40}$/.test(name)) radius[name] = size;
  }

  const fonts: Record<string, string> = {};
  for (const [role, value] of Object.entries((doc.fonts ?? {}) as Record<string, unknown>)) {
    const spec = value as Record<string, unknown> | string;
    const family = typeof spec === "string" ? spec : String((spec as Record<string, unknown>)?.family ?? "");
    const stack = typeof spec === "object" ? String((spec as Record<string, unknown>)?.stack ?? "") : "";
    const whole = [family, stack].filter(Boolean).join(", ");
    if (whole && /^[\w\s,"'-]{1,120}$/.test(whole) && /^[\w-]{1,40}$/.test(role)) fonts[role] = whole;
  }

  const space = Array.isArray(doc.space)
    ? doc.space.map(Number).filter((n) => Number.isFinite(n) && n > 0).sort((a, b) => a - b)
    : [];

  return {
    name: typeof doc.name === "string" ? doc.name : "Design system",
    colors,
    text,
    radius,
    fonts,
    space: space.length ? space : [4, 8, 12, 16, 24, 32, 48, 64],
    components: components(doc.components),
    conventions: "",
  };
}

/**
 * A deck's brand profile, adapted.
 *
 * Its type scale is in points for a 1920-wide slide. Converted by ratio against body rather than
 * by the profile's own `px_per_pt`, because a screen is not a slide and print calibration would
 * size a paragraph for a projector.
 */
const BODY_PT = 12;
const BODY_PX = 16;

function fromBrandProfile(doc: Record<string, unknown>): System | null {
  const tokens = doc.tokens as Record<string, unknown> | undefined;
  if (!tokens || typeof tokens !== "object") return null;
  const colorsIn = tokens.colors;
  if (!colorsIn || typeof colorsIn !== "object") return null;

  const colors: Record<string, string> = {};
  for (const [name, value] of Object.entries(colorsIn as Record<string, unknown>)) {
    const hex = colour(value);
    if (hex && /^[\w-]{1,40}$/.test(name)) colors[name] = hex;
  }
  if (!Object.keys(colors).length) return null;

  const text: Record<string, TextRole> = {};
  for (const [role, value] of Object.entries((tokens.type ?? {}) as Record<string, unknown>)) {
    const spec = value as Record<string, unknown>;
    const pt = Number(spec?.size_pt);
    if (!Number.isFinite(pt) || !/^[\w-]{1,40}$/.test(role)) continue;
    const tracking = Number(spec?.tracking_pct);
    text[role] = {
      size: `${Math.round((pt / BODY_PT) * BODY_PX)}px`,
      weight: WEIGHTS[String(spec?.weight ?? "").toLowerCase()],
      transform: spec?.case === "upper" ? "uppercase" : undefined,
      tracking: Number.isFinite(tracking) && tracking ? `${(tracking / 100).toFixed(3)}em` : undefined,
    };
  }

  const radius: Record<string, string> = {};
  for (const [name, value] of Object.entries((doc.radii_px ?? tokens.radii_px ?? {}) as Record<string, unknown>)) {
    const size = px(value);
    if (size && /^[\w-]{1,40}$/.test(name)) radius[name] = size;
  }

  const fonts: Record<string, string> = {};
  const families = (tokens.fonts ?? {}) as Record<string, unknown>;
  for (const [role, value] of Object.entries(families)) {
    const family = String(value ?? "").trim();
    if (family && /^[\w\s-]{1,60}$/.test(family) && /^[\w-]{1,40}$/.test(role)) {
      fonts[role] = `"${family}", system-ui, sans-serif`;
    }
  }

  return {
    name: String(doc.name ?? doc.brand ?? "Brand"),
    colors,
    text,
    radius,
    fonts,
    space: [4, 8, 12, 16, 24, 32, 48, 64],
    derived: true,
    components: components(doc.components),
    conventions: "",
  };
}

/** The deck's weight names, as CSS numbers. */
const WEIGHTS: Record<string, number> = {
  thin: 100,
  extralight: 200,
  light: 300,
  regular: 400,
  normal: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
  extrabold: 800,
  black: 900,
};

/**
 * Whichever system that file holds, or null.
 *
 * Sniffed rather than declared, for the same reason the editor is offered by detection: a file
 * that has to announce its own format is a file somebody has to remember to label.
 */
export function readSystem(text: string, conventions = ""): System | null {
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch {
    return null;
  }
  if (!doc || typeof doc !== "object") return null;
  const record = doc as Record<string, unknown>;
  const system = fromDesignSystem(record) ?? fromBrandProfile(record);
  if (system && conventions) system.conventions = conventions;
  return system;
}

/**
 * Kith's own nine, as an instance of the same thing.
 *
 * Not imported from `frameTokens()`. That function is the sealed frame's contract — the one
 * place both ends of the frame bridge agree — and widening it for a second consumer is how the
 * theme silently stopped travelling last time. This reads the palette directly instead.
 */
export function kithSystem(palette: Record<string, string>): System {
  return {
    name: "Kith",
    colors: {
      bg: palette.surface,
      line: palette.line,
      text: palette.text,
      dim: palette.dim,
      accent: palette.accent,
      "accent-soft": palette.accentSoft,
      second: palette.second,
      "second-soft": palette.secondSoft,
      muted: palette.muted,
    },
    text: {
      title: { size: "24px", weight: 600 },
      body: { size: "14px", weight: 400 },
      small: { size: "12px", weight: 400 },
    },
    radius: { chip: "8px", card: "12px" },
    fonts: { body: "ui-sans-serif, system-ui, sans-serif" },
    space: [4, 8, 12, 16, 24, 32, 48, 64],
    components: {},
    conventions: "",
  };
}

/**
 * The system as custom properties, ready to put on the design root.
 *
 * Namespaced `--ds-` so a system can never collide with the app's own variables — the editor
 * draws its selection outline and drop indicators with `var(--kith)`, inherited into the design
 * subtree, and a system free to define any name could switch the editor's own affordances off
 * inside the thing being edited.
 */
export function tokensFor(system: System): Record<string, string> {
  const style: Record<string, string> = {};
  for (const [name, value] of Object.entries(system.colors)) style[`--ds-${name}`] = value;
  for (const [name, value] of Object.entries(system.radius)) style[`--ds-radius-${name}`] = value;
  for (const [role, value] of Object.entries(system.fonts)) style[`--ds-font-${role}`] = value;
  for (const [role, spec] of Object.entries(system.text)) {
    style[`--ds-text-${role}-size`] = spec.size;
    if (spec.weight) style[`--ds-text-${role}-weight`] = String(spec.weight);
    if (spec.tracking) style[`--ds-text-${role}-tracking`] = spec.tracking;
  }
  return style;
}

/** The colour tokens as the inspector offers them: a reference and the colour it resolves to. */
export function swatches(system: System): { reference: string; value: string; name: string }[] {
  return Object.entries(system.colors).map(([name, value]) => ({
    name,
    value,
    reference: `var(--ds-${name})`,
  }));
}
