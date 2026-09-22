/**
 * Reading a design system, including the one that existed before this surface did.
 *
 * The adapter is the part worth asserting. `brand/profile.json` was authored for slides — its
 * type scale is in points against a 1920-wide deck — and the temptation is to honour the
 * profile's own `px_per_pt`, which would size a paragraph for a projector. Body is the anchor
 * instead, and these tests pin the ratio so a later "simplification" back to the calibration
 * figure fails here rather than in somebody's mockup.
 *
 * The file is also untrusted: it lives in his workspace, so anything in it can be anything.
 */
import { describe, expect, it } from "vitest";

import { kithSystem, readSystem, swatches, tokensFor } from "@/lib/design/system";
import { PALETTE } from "@/lib/kith-palette";

const WRITTEN = JSON.stringify({
  name: "Sadeef",
  colors: { accent: "#59C5C7", ink: "#343C42" },
  text: { title: { size: 31, weight: 700 }, eyebrow: { size: "9px", case: "upper", tracking: "0.2em" } },
  radius: { card: 20 },
  fonts: { display: { family: "Urbanist", stack: "system-ui, sans-serif" } },
  space: [4, 8, 16, 24],
});

/** The real shape, trimmed: bare hex, points, weight names, tracking in percent. */
const PROFILE = JSON.stringify({
  name: "Sadeef",
  tokens: {
    colors: { ink: "343C42", accent: "59C5C7", surface: "FFFFFF" },
    fonts: { en: "Urbanist", ar: "TheSansArabic Plain" },
    type: {
      hero: { size_pt: 43, weight: "Light" },
      body: { size_pt: 12, weight: "Regular" },
      eyebrow: { size_pt: 7, weight: "Bold", case: "upper", tracking_pct: 20 },
    },
  },
  radii_px: { chip: 10, card: 20 },
  calibration: { px_per_pt: 2.004 },
});

describe("a written design-system.json", () => {
  it("reads colours, type, radii and fonts", () => {
    const system = readSystem(WRITTEN)!;
    expect(system.name).toBe("Sadeef");
    expect(system.colors.accent).toBe("#59C5C7");
    expect(system.text.title).toMatchObject({ size: "31px", weight: 700 });
    expect(system.radius.card).toBe("20px");
    expect(system.fonts.display).toContain("Urbanist");
    expect(system.space).toEqual([4, 8, 16, 24]);
  });

  it("turns an upper-case role into a transform rather than leaving it as prose", () => {
    expect(readSystem(WRITTEN)!.text.eyebrow.transform).toBe("uppercase");
  });

  it("is not marked derived, because he wrote it", () => {
    expect(readSystem(WRITTEN)!.derived).toBeUndefined();
  });
});

describe("the brand profile that already existed", () => {
  const system = readSystem(PROFILE)!;

  it("reads a bare hex as a colour", () => {
    expect(system.colors.accent).toBe("#59C5C7");
    expect(system.colors.ink).toBe("#343C42");
  });

  it("converts points by ratio against body, not by the deck's calibration", () => {
    // 12pt body is 16px. Everything else scales from that: 43pt hero → 57px.
    // The profile's own px_per_pt of 2.004 would make body 24px and hero 86px — type for a
    // projector, in a 1200px-wide screen.
    expect(system.text.body.size).toBe("16px");
    expect(system.text.hero.size).toBe("57px");
    expect(system.text.eyebrow.size).toBe("9px");
  });

  it("turns weight names into numbers", () => {
    expect(system.text.hero.weight).toBe(300);
    expect(system.text.eyebrow.weight).toBe(700);
  });

  it("turns tracking percent into em", () => {
    expect(system.text.eyebrow.tracking).toBe("0.200em");
  });

  it("says the numbers were converted", () => {
    // So the inspector can say where they came from rather than implying he chose them.
    expect(system.derived).toBe(true);
  });

  it("carries the fonts through as families", () => {
    expect(system.fonts.en).toContain("Urbanist");
    expect(system.fonts.ar).toContain("TheSansArabic Plain");
  });
});

describe("a file that is not a system", () => {
  it("refuses rather than inventing one", () => {
    expect(readSystem("not json")).toBeNull();
    expect(readSystem("{}")).toBeNull();
    expect(readSystem(JSON.stringify({ colors: {} }))).toBeNull();
    expect(readSystem(JSON.stringify([1, 2, 3]))).toBeNull();
  });

  it("drops a token name that could not be written as a custom property", () => {
    const system = readSystem(JSON.stringify({ colors: { "ok": "#fff", "not ok!": "#000" } }))!;
    expect(Object.keys(system.colors)).toEqual(["ok"]);
  });

  it("drops a colour that is not one", () => {
    const system = readSystem(JSON.stringify({ colors: { a: "#fff", b: "url(http://x)" } }))!;
    expect(Object.keys(system.colors)).toEqual(["a"]);
  });
});

describe("tokensFor", () => {
  const system = readSystem(WRITTEN)!;
  const style = tokensFor(system);

  it("namespaces everything under --ds-", () => {
    // The editor draws its own selection outline with var(--kith), inherited into the design. A
    // system free to define any name could switch the editor's affordances off inside the thing
    // being edited.
    expect(Object.keys(style).every((name) => name.startsWith("--ds-"))).toBe(true);
  });

  it("emits colours, radii, fonts and type", () => {
    expect(style["--ds-accent"]).toBe("#59C5C7");
    expect(style["--ds-radius-card"]).toBe("20px");
    expect(style["--ds-font-display"]).toContain("Urbanist");
    expect(style["--ds-text-title-size"]).toBe("31px");
    expect(style["--ds-text-title-weight"]).toBe("700");
  });
});

describe("Kith's own nine, as one system among others", () => {
  const system = kithSystem(PALETTE.dark);

  it("is a system rather than a special case", () => {
    expect(system.colors.accent).toBe(PALETTE.dark.accent);
    expect(swatches(system).map((one) => one.reference)).toContain("var(--ds-accent)");
  });
});

/**
 * The shared fixture, read by the third reader.
 *
 * There are three readers of a design system and no way to get to one: this, the service in
 * `server/kith/services/design_system.py`, and the standalone `check_design.py` handed to the
 * model as a path. The Python pair are held to each other by
 * `server/tests/test_three_readers_agree_on_one_system.py`; this block is the same fixture,
 * with the same expected values, so a change to what a colour or a component *means* fails in
 * both languages instead of in somebody's mockup.
 *
 * Keep the object below byte-identical to `FIXTURE` in that file.
 */
const SHARED = JSON.stringify({
  name: "Vigil",
  colors: { ink: "1b1f23", panel: "#ffffff", accent: "rgb(47, 111, 79)" },
  text: { hero: { size: 44, weight: 700 }, body: { size: 16 } },
  radius: { card: 12 },
  space: [4, 8, 12, 16, 24, 40],
  components: {
    "button.primary": { html: '<button data-k-component="button.primary">Save</button>', use: "Commit." },
    "bad name!": { html: "<div>never</div>" },
    scripted: { html: "<div><script>x</script></div>" },
  },
});

describe("the fixture the Python readers are also held to", () => {
  const system = readSystem(SHARED)!;

  it("turns a bare hex into a colour the same way", () => {
    expect(system.colors.ink).toBe("#1b1f23");
  });

  it("keeps a CSS colour function as written", () => {
    expect(system.colors.accent).toBe("rgb(47, 111, 79)");
  });

  it("measures against the system's own spacing scale", () => {
    expect(system.space).toEqual([4, 8, 12, 16, 24, 40]);
  });

  it("refuses exactly the components the service refuses", () => {
    // A part one reader stores and the other ignores is a part that renders in the app and
    // fails the check, for a reason nobody can see.
    expect(Object.keys(system.components)).toEqual(["button.primary"]);
    expect(system.components["button.primary"].use).toBe("Commit.");
  });

  it("carries the conventions handed to it beside the file", () => {
    expect(readSystem(SHARED, "Borders, never shadows.")!.conventions).toBe("Borders, never shadows.");
  });
});
