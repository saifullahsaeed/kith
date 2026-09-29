import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import ts from "typescript";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

/**
 * The page scripts, run against a real page in the Chromium that ships them.
 *
 * `page-scripts.test.ts` checks that they parse, which is all that was ever checked, and every
 * one of the failures this file exists for parsed fine: a click on a button below the fold that
 * reported success and pressed nothing, `type(into: "Work email")` missing the `<label>` and
 * typing into whatever had focus, a rich-text editor that took no text, a Tab that moved no
 * focus, and a picked card whose selector resolved to a different card. So each is asserted here
 * by what it does to a page — in a hidden view, the way the model usually drives one.
 *
 * One Electron launch for the whole file, through `scripts/page-harness.js`. Skipped where there
 * is no Electron binary to launch, rather than failing on an environment.
 */

const root = join(__dirname, "../..");
const electron = (() => {
  try {
    return join(root, "node_modules/electron/dist", readFileSync(join(root, "node_modules/electron/path.txt"), "utf8"));
  } catch {
    return "";
  }
})();

const FORM = `<!doctype html><html><body style="margin:0;font:14px sans-serif">
<form id="signin" onsubmit="event.preventDefault(); window.submitted = (window.submitted || 0) + 1">
  <label for="email">Work email</label> <input id="email" name="email_address">
  <label>Password <input id="pw" type="password"></label>
  <input id="plain" placeholder="Search">
  <input id="chat" aria-label="Chat" onkeydown="if (event.key === 'Enter') { event.preventDefault(); window.sent = true; }">
  <select id="country" aria-label="Country"><option value="">Choose</option><option value="ca">Canada</option><option value="fr">France</option></select>
  <button id="go" type="submit">Sign in</button>
</form>
<button id="toggle" type="button" onclick="window.toggled = true">Toggle</button>
<div id="rich" contenteditable="true" aria-label="Message" style="min-height:30px;border:1px solid"></div>
<script>document.getElementById("rich").addEventListener("beforeinput", (e) => { window.richBefore = e.inputType; });</script>
<div id="cards">
  <div data-testid="card"><h3>Alpha</h3></div>
  <div data-testid="card"><h3>Beta</h3></div>
  <div data-testid="card"><h3>Gamma</h3></div>
  <div data-test-id="tile"><h3>One</h3></div>
  <div data-test-id="tile"><h3>Two</h3></div>
</div>
<button id="menu" type="button" onpointerdown="window.menuOpened = (window.menuOpened || 0) + 1" onmousedown="window.menuOpened = (window.menuOpened || 0) + 1">Menu</button>
<div style="height:2400px"></div>
<button id="far" type="button" onclick="window.farClicked = true">Place order</button>
</body></html>`;

const COVERED = `<!doctype html><html><body style="margin:0">
<button id="under" onclick="window.underClicked = true" style="position:absolute;top:100px;left:100px">Submit</button>
<div onclick="window.bannerClicked = true" style="position:fixed;top:0;left:0;right:0;height:300px;background:#eee">Accept cookies</div>
</body></html>`;

const SHELL = `<!doctype html><html><body style="margin:0;height:100vh;overflow:hidden">
<div id="panel" style="height:100vh;overflow:auto"><div style="height:3000px">a long inbox</div></div>
</body></html>`;

/** Each case is a page and the body of an async function the harness runs against it. */
const CASES: Record<string, { html: string; run: string }> = {
  clickBelowTheFold: {
    html: FORM,
    run: `
      const at = await run(scripts.locate("", "Place order"), false);
      const hit = await run(scripts.dispatchClick(at.x, at.y));
      return { at, hit, clicked: await run("window.farClicked === true", false) };`,
  },
  clickAtNothing: {
    html: FORM,
    run: `return await run(scripts.dispatchClick(5000, 5000));`,
  },
  clickUnderABanner: {
    html: COVERED,
    run: `
      const at = await run(scripts.locate("", "Submit"), false);
      const hit = await run(scripts.dispatchClick(at.x, at.y));
      return { at, hit, under: await run("!!window.underClicked", false), banner: await run("!!window.bannerClicked", false) };`,
  },
  typeByLabelReplaces: {
    html: FORM,
    run: `
      await run('document.getElementById("email").value = "old@example.com"; true');
      const field = await run(scripts.focusField("Work email"));
      await insertText("saif@example.com");
      const done = await run(scripts.typed("saif@example.com", field.before, true));
      return { field, done, value: await run('document.getElementById("email").value', false) };`,
  },
  typeSameTextTwice: {
    html: FORM,
    run: `
      await run('document.getElementById("email").value = "a@b.co"; true');
      const field = await run(scripts.focusField("Work email"));
      await insertText("a@b.co");
      await run(scripts.typed("a@b.co", field.before, true));
      return await run('document.getElementById("email").value', false);`,
  },
  typeByWrappingLabel: {
    html: FORM,
    run: `
      const field = await run(scripts.focusField("Password"));
      return { field, focused: await run("document.activeElement.id", false) };`,
  },
  typeBySelector: {
    html: FORM,
    run: `
      await run(scripts.focusField("input[name=email_address]"));
      const one = await run("document.activeElement.id", false);
      await run(scripts.focusField("#plain"));
      return [one, await run("document.activeElement.id", false)];`,
  },
  typeIntoNothing: {
    html: FORM,
    run: `return await run(scripts.focusField("Phone number"));`,
  },
  typeUnfocused: {
    html: FORM,
    run: `
      await run("document.activeElement && document.activeElement.blur(); true");
      return await run(scripts.focusField(""));`,
  },
  typeRich: {
    html: FORM,
    run: `
      const field = await run(scripts.focusField("Message"));
      await insertText("hello there");
      const done = await run(scripts.typed("hello there", field.before, true));
      return { field, done, text: await run('document.getElementById("rich").innerText', false), before: await run("window.richBefore", false) };`,
  },
  chooseFromASelect: {
    html: FORM,
    run: `
      const field = await run(scripts.focusField("Country"));
      const chose = await run(scripts.chooseOption("canada"));
      const value = await run('document.getElementById("country").value', false);
      const miss = await run(scripts.chooseOption("Mars"));
      return { field, chose, value, miss };`,
  },
  pressTab: {
    html: FORM,
    run: `
      await run('document.getElementById("email").focus(); true');
      const answer = await run(scripts.pressKey(scripts.KEYS.Tab));
      return { answer, focused: await run("document.activeElement.id", false) };`,
  },
  pressBackspace: {
    html: FORM,
    run: `
      await run('const el = document.getElementById("email"); el.value = "abc"; el.focus(); el.setSelectionRange(3, 3); true');
      const answer = await run(scripts.pressKey(scripts.KEYS.Backspace));
      return { answer, value: await run('document.getElementById("email").value', false) };`,
  },
  pressEnterInAField: {
    html: FORM,
    run: `
      await run('document.getElementById("email").focus(); true');
      const answer = await run(scripts.pressKey(scripts.KEYS.Enter));
      return { answer, submitted: await run("window.submitted || 0", false) };`,
  },
  pressEnterOnAButton: {
    html: FORM,
    run: `
      await run('document.getElementById("toggle").focus(); true');
      const answer = await run(scripts.pressKey(scripts.KEYS.Enter));
      return { answer, toggled: await run("!!window.toggled", false) };`,
  },
  pressEnterThePageHandles: {
    html: FORM,
    run: `
      await run('document.getElementById("chat").focus(); true');
      const answer = await run(scripts.pressKey(scripts.KEYS.Enter));
      return { answer, sent: await run("!!window.sent", false), submitted: await run("window.submitted || 0", false) };`,
  },
  pressArrowInASelect: {
    html: FORM,
    run: `
      await run('document.getElementById("country").focus(); true');
      const answer = await run(scripts.pressKey(scripts.KEYS.ArrowDown));
      return { answer, value: await run('document.getElementById("country").value', false) };`,
  },
  selectorsForTwins: {
    html: FORM,
    run: `
      const out = {};
      for (const name of ["Alpha", "Beta", "Gamma", "Two"]) {
        const box = await run(scripts.boxOf("", name), false);
        out[name] = { selector: box.selector, resolves: await run("document.querySelector(" + JSON.stringify(box.selector) + ").innerText.trim()", false) };
      }
      return out;`,
  },
  boxBelowTheFold: {
    html: FORM,
    run: `return await run(scripts.boxOf("", "Place order"), false);`,
  },
  pickSwallowsThePress: {
    html: FORM,
    run: `
      await run("window.__picking = " + scripts.picker() + "; true");
      await run(\`(() => {
        const el = document.getElementById("menu");
        el.scrollIntoView({ block: "center", behavior: "instant" });
        const r = el.getBoundingClientRect();
        const at = { bubbles: true, cancelable: true, clientX: r.left + 5, clientY: r.top + 5, view: window };
        el.dispatchEvent(new PointerEvent("pointerdown", at));
        el.dispatchEvent(new MouseEvent("mousedown", at));
        el.dispatchEvent(new PointerEvent("pointerup", at));
        el.dispatchEvent(new MouseEvent("mouseup", at));
        el.dispatchEvent(new MouseEvent("click", at));
        return true;
      })()\`);
      const picked = await run("window.__picking", false);
      return { picked, opened: await run("window.menuOpened || 0", false) };`,
  },
  scrollAnAppShell: {
    html: SHELL,
    run: `return await run(scripts.scrollPage(600));`,
  },
  scrollAPage: {
    html: FORM,
    run: `return await run(scripts.scrollPage(600));`,
  },
};

let results: Record<string, unknown> = {};
let scratch = "";

describe.skipIf(!electron || !existsSync(electron))("the page scripts, in real Chromium", () => {
  beforeAll(() => {
    scratch = mkdtempSync(join(tmpdir(), "kith-page-scripts-"));
    // The scripts as CommonJS, for the harness to `require` — transpiled rather than taken from
    // `out/`, so this never tests a stale build.
    const source = readFileSync(join(__dirname, "page-scripts.ts"), "utf8");
    const built = ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    });
    writeFileSync(join(scratch, "page-scripts.js"), built.outputText);
    writeFileSync(join(scratch, "job.json"), JSON.stringify({ scripts: join(scratch, "page-scripts.js"), cases: CASES }));
    const env = { ...process.env };
    // Set in some shells, and it makes the binary a plain Node that has no `app` to start.
    delete env.ELECTRON_RUN_AS_NODE;
    execFileSync(
      electron,
      [join(root, "scripts/page-harness.js"), join(scratch, "job.json"), join(scratch, "results.json")],
      { env, timeout: 90_000, stdio: "ignore" },
    );
    results = JSON.parse(readFileSync(join(scratch, "results.json"), "utf8")) as Record<string, unknown>;
  }, 120_000);

  afterAll(() => {
    if (scratch) rmSync(scratch, { recursive: true, force: true });
  });

  // Any case the harness could not run is a failure of the case, said by name.
  const result = <T,>(name: string): T => {
    const got = results[name] as { harnessError?: string } | undefined;
    expect(got?.harnessError, `${name} did not run`).toBeUndefined();
    return got as T;
  };

  describe("click", () => {
    it("scrolls to a button below the fold, and presses it", () => {
      const got = result<{ at: { y: number; covered: string }; hit: string; clicked: boolean }>("clickBelowTheFold");
      expect(got.at.y).toBeGreaterThan(0);
      expect(got.at.y).toBeLessThan(800);
      expect(got.at.covered).toBe("");
      expect(got.hit).toBe("Place order");
      expect(got.clicked).toBe(true);
    });

    it("answers null for a point with nothing at it, which the caller reports as a miss", () => {
      expect(result("clickAtNothing")).toBeNull();
    });

    it("names what is on top, and says what the click actually hit", () => {
      const got = result<{ at: { covered: string }; hit: string; under: boolean; banner: boolean }>("clickUnderABanner");
      expect(got.at.covered).toBe("Accept cookies");
      expect(got.hit).toBe("Accept cookies");
      expect(got.under).toBe(false);
      expect(got.banner).toBe(true);
    });
  });

  describe("type", () => {
    it("finds a field by its <label for>, and replaces what was in it", () => {
      const got = result<{ field: { name: string; kind: string }; done: { landed: boolean }; value: string }>("typeByLabelReplaces");
      expect(got.field).toMatchObject({ name: "Work email", kind: "text" });
      expect(got.done.landed).toBe(true);
      expect(got.value).toBe("saif@example.com");
    });

    it("does not type it twice when the field already held exactly that", () => {
      expect(result("typeSameTextTwice")).toBe("a@b.co");
    });

    it("finds a field inside the label that wraps it", () => {
      const got = result<{ field: { name: string }; focused: string }>("typeByWrappingLabel");
      expect(got.field.name).toBe("Password");
      expect(got.focused).toBe("pw");
    });

    it("takes a selector, not only one starting with #", () => {
      expect(result("typeBySelector")).toEqual(["email", "plain"]);
    });

    it("lists the fields instead of typing somewhere else when the name matches none", () => {
      const got = result<{ fields: string[] }>("typeIntoNothing");
      expect(got.fields).toEqual(expect.arrayContaining(["Work email", "Password", "Search", "Country"]));
    });

    it("refuses when nothing is focused, rather than typing into the void", () => {
      expect(result("typeUnfocused")).toMatchObject({ problem: "unfocused" });
    });

    it("types into a rich-text editor, through beforeinput the way a keyboard does", () => {
      const got = result<{ field: { kind: string }; done: { landed: boolean }; text: string; before: string }>("typeRich");
      expect(got.field.kind).toBe("rich");
      expect(got.done.landed).toBe(true);
      expect(got.text.trim()).toBe("hello there");
      expect(got.before).toBe("insertText");
    });

    it("chooses an option in a select, and lists the choices when none match", () => {
      const got = result<{ field: { kind: string }; chose: { chose: string }; value: string; miss: { chose: string; choices: string[] } }>("chooseFromASelect");
      expect(got.field.kind).toBe("select");
      expect(got.chose.chose).toBe("Canada");
      expect(got.value).toBe("ca");
      expect(got.miss.chose).toBe("");
      expect(got.miss.choices).toEqual(["Choose", "Canada", "France"]);
    });
  });

  describe("press", () => {
    it("moves focus on Tab", () => {
      const got = result<{ answer: { did: string }; focused: string }>("pressTab");
      expect(got.focused).toBe("pw");
      expect(got.answer.did).toBe("moved focus to Password");
    });

    it("deletes on Backspace", () => {
      const got = result<{ answer: { did: string }; value: string }>("pressBackspace");
      expect(got.value).toBe("ab");
      expect(got.answer.did).toBe("deleted");
    });

    it("submits a field's form on Enter", () => {
      const got = result<{ answer: { did: string }; submitted: number }>("pressEnterInAField");
      expect(got.submitted).toBe(1);
      expect(got.answer.did).toBe("submitted the form");
    });

    it("presses a focused button on Enter", () => {
      const got = result<{ answer: { did: string }; toggled: boolean }>("pressEnterOnAButton");
      expect(got.toggled).toBe(true);
      expect(got.answer.did).toBe("pressed Toggle");
    });

    it("leaves Enter to a page that handles it, and does not submit on top", () => {
      const got = result<{ answer: { did: string }; sent: boolean; submitted: number }>("pressEnterThePageHandles");
      expect(got.sent).toBe(true);
      expect(got.submitted).toBe(0);
      expect(got.answer.did).toBe("the page handled it");
    });

    it("moves through a select on an arrow key", () => {
      const got = result<{ answer: { did: string }; value: string }>("pressArrowInASelect");
      expect(got.value).toBe("ca");
      expect(got.answer.did).toBe("chose Canada");
    });
  });

  describe("pictures and picking", () => {
    it("gives each of several same-testid cards a selector that resolves to that card", () => {
      const got = result<Record<string, { selector: string; resolves: string }>>("selectorsForTwins");
      for (const name of ["Alpha", "Beta", "Gamma", "Two"]) expect(got[name]?.resolves).toBe(name);
      // The data-test-id spelling is written as itself, not as data-testid.
      expect(got.Two?.selector).toContain("data-test-id");
    });

    it("scrolls a component into view before measuring it for a crop", () => {
      const got = result<{ y: number; height: number }>("boxBelowTheFold");
      expect(got.y).toBeGreaterThanOrEqual(0);
      expect(got.y + got.height).toBeLessThanOrEqual(800);
    });

    it("picks a control without letting its pointerdown or mousedown fire", () => {
      const got = result<{ picked: { selector: string } | null; opened: number }>("pickSwallowsThePress");
      expect(got.picked?.selector).toBe("#menu");
      expect(got.opened).toBe(0);
    });
  });

  describe("scroll", () => {
    it("scrolls the panel of an app whose body does not scroll", () => {
      expect(result("scrollAnAppShell")).toEqual({ moved: 600, where: "panel" });
    });

    it("scrolls the page when the page is what scrolls", () => {
      expect(result("scrollAPage")).toEqual({ moved: 600, where: "page" });
    });
  });
});
