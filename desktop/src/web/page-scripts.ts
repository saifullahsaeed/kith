/**
 * The scripts a browser pane runs **inside the page** — finding things, clicking them, typing
 * into them, pointing at them.
 *
 * Out of `web-views.ts` for one reason: that file is Electron end to end, and these are the part
 * that can be held to account in a real page without it. `page-scripts.dom.test.ts` loads each
 * of them into Chromium and checks what they *do*; before that, the only test of them was that
 * they parsed, and five of them reported success while doing nothing — a click below the fold,
 * a field named by its label, a rich-text editor, a Tab key, a selector that matched the wrong
 * card. Every one of those looked fine to a parser.
 *
 * ## The rule every script here follows
 *
 * **Say what happened, including when nothing did.** A script that returns `true` after an
 * action that never landed is the worst failure this browser has, because the model reads the
 * answer, believes it, and builds the next five calls on a page that did not move. So each one
 * answers with what it found, what it hit, or why it could not.
 *
 * Page text reaches these only through `JSON.stringify`, never by interpolation, so a selector
 * or a label cannot become code in Kith's own injected script.
 */

/** Everything a person would call clickable, and the order `locate` looks through it in. */
const CLICKABLE =
  "a, button, input, select, textarea, summary, label, [role=button], [role=link], [role=tab], " +
  "[role=menuitem], [role=option], [role=checkbox], [role=radio], [role=switch], [onclick], [tabindex]";

/**
 * The part every finder needs: is it on the page, and bring it where a pointer can reach it.
 *
 * `intoView` is the fix for the worst of the silent failures. `elementFromPoint` answers `null`
 * for a point outside the viewport, so a button below the fold was located, "clicked" at a
 * coordinate nothing occupied, and reported as clicked. A person scrolls before they click;
 * this does the same, and only when the element is not already fully in view, so a click on
 * something already on screen does not move the page under the person watching it.
 *
 * `behavior: "instant"` rather than `"auto"`, because `auto` honours a page's
 * `scroll-behavior: smooth` — and a rectangle measured mid-animation is the wrong rectangle.
 */
const FIND = `
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    const squash = (s) => String(s || "").replace(/\\s+/g, " ").trim();
    const intoView = (el) => {
      const r = el.getBoundingClientRect();
      if (r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth) return;
      el.scrollIntoView({ block: r.height > innerHeight ? "start" : "center", inline: "nearest", behavior: "instant" });
    };
    const query = (css) => {
      try {
        return { el: css ? document.querySelector(css) : null, bad: false };
      } catch (_) {
        return { el: null, bad: true };
      }
    };
`;

/**
 * A selector you can hand back, for `snap` and `pick`.
 *
 * Built by walking up from the element and stopping the moment the chain is **unique in the
 * document**, rather than by climbing a fixed number of levels. A chain that is not unique is
 * worse than no selector at all: it looks specific, `document.querySelector` answers with the
 * first thing that matches it, and the next call acts on some other card on the same page.
 *
 * **A `data-testid` is a part, not a stopping point.** It used to end the walk the moment one
 * was found, and a list of nine cards each marked `data-testid="card"` gave all nine the same
 * selector — which resolved to the first. Picking one card among nine is the whole reason the
 * crosshair exists. An `id` still ends the walk, but only once it is checked to be unique, and
 * both are written through `CSS.escape` so an id that starts with a digit or carries a quote is
 * still a selector.
 */
const SELECTOR_FOR = `
    const selectorFor = (el) => {
      if (!el || !el.tagName) return "";
      const unique = (css) => {
        try {
          return document.querySelectorAll(css).length === 1;
        } catch (_) {
          return false;
        }
      };
      const parts = [];
      let node = el;
      let climbed = 0;
      while (node && node.nodeType === 1 && node !== document.documentElement && climbed < 8) {
        if (node.id && unique("#" + CSS.escape(node.id))) {
          parts.unshift("#" + CSS.escape(node.id));
          break;
        }
        const attr = node.hasAttribute("data-testid") ? "data-testid" : node.hasAttribute("data-test-id") ? "data-test-id" : "";
        let part = node.tagName.toLowerCase();
        if (attr) part += "[" + attr + '="' + CSS.escape(node.getAttribute(attr) || "") + '"]';
        const parent = node.parentElement;
        if (parent) {
          const twins = [...parent.children].filter((one) => one.matches(part));
          if (twins.length > 1) {
            const sameTag = [...parent.children].filter((one) => one.tagName === node.tagName);
            part += ":nth-of-type(" + (sameTag.indexOf(node) + 1) + ")";
          }
        }
        parts.unshift(part);
        // Stop as soon as it identifies one thing. Climbing further only makes it longer and
        // more brittle, and a shorter selector is one a person can read.
        if (unique(parts.join(" > "))) break;
        node = parent;
        climbed += 1;
      }
      return parts.join(" > ");
    };
    const labelFor = (el) =>
      ((el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("alt") || el.title || "")
        .replace(/\\s+/g, " ")
        .trim()
        .slice(0, 120));
`;

/** What `locate` answers with. */
export type Located =
  | { x: number; y: number; label: string; covered: string; role: string }
  | { options: string[] }
  | { unreachable: string }
  | { invalid: string };

/**
 * Where to click, found by what it says or by a selector — and scrolled to first.
 *
 * The failure path is the useful half: a model that cannot find "Sign in" gets the list of what
 * *is* clickable and can pick, rather than taking a screenshot to find out and spending a few
 * thousand tokens to read it.
 *
 * Two things it now says that it did not. `covered` names whatever is actually on top at the
 * point — a cookie banner, a sticky header — because that is what the click will hit, and an
 * answer that says "clicked Submit" when the banner took it is the same lie as a click that
 * never landed. And a loose match prefers the **shortest** label that contains the words, so
 * `click(text: "Sign in")` finds the button and not the card that happens to mention it.
 */
export function locate(selector: string, text: string): string {
  const wanted = JSON.stringify(text.toLowerCase());
  const css = JSON.stringify(selector);
  return `(() => {
    ${FIND}
    const label = (el) =>
      squash(el.innerText || el.value || el.getAttribute("aria-label") || el.name || el.placeholder || el.title).slice(0, 60);
    /* The role a test would find it by — \`getByRole("button", { name })\` survives a restyle that a
     * CSS path does not. Only the roles worth naming; anything else is found by its text. */
    const roleOf = (el) => {
      const said = el.getAttribute("role");
      if (said) return said;
      if (el.tagName === "A" && el.hasAttribute("href")) return "link";
      if (el.tagName === "BUTTON" || el.tagName === "SUMMARY") return "button";
      if (el.tagName === "INPUT") {
        return { button: "button", submit: "button", reset: "button", checkbox: "checkbox", radio: "radio" }[el.type] || "";
      }
      return "";
    };
    const point = (el) => {
      intoView(el);
      const r = el.getBoundingClientRect();
      const left = Math.max(0, r.left);
      const right = Math.min(innerWidth, r.right);
      const top = Math.max(0, r.top);
      const bottom = Math.min(innerHeight, r.bottom);
      if (right <= left || bottom <= top) return null;
      const x = Math.round((left + right) / 2);
      const y = Math.round((top + bottom) / 2);
      const hit = document.elementFromPoint(x, y);
      const mine =
        hit &&
        (hit === el || el.contains(hit) || hit.control === el || (el.labels && [...el.labels].includes(hit)));
      return { x, y, covered: hit && !mine ? label(hit) || hit.tagName.toLowerCase() : "" };
    };
    const asked = query(${css});
    if (asked.bad) return { invalid: ${css} };
    const clickable = [...document.querySelectorAll(${JSON.stringify(CLICKABLE)})].filter(visible);
    let el = asked.el && visible(asked.el) ? asked.el : null;
    if (!el && ${wanted}) {
      el =
        clickable.find((one) => label(one).toLowerCase() === ${wanted}) ||
        clickable
          .filter((one) => label(one).toLowerCase().includes(${wanted}))
          .sort((a, b) => label(a).length - label(b).length)[0] ||
        null;
    }
    if (el) {
      const at = point(el);
      return at ? { ...at, label: label(el) || ${css}, role: roleOf(el) } : { unreachable: label(el) || ${css} };
    }
    return { options: clickable.map(label).filter(Boolean).slice(0, 25) };
  })()`;
}

/**
 * Where a named thing *is*, as a rectangle, scrolled into view first. `locate` answers with a
 * point to click; a picture needs the whole box, and the two disagreeing about which element they
 * found would be the worst kind of bug here — a crop of one component and a click on another.
 *
 * The scroll is the fix for a crop below the fold: its rectangle was clamped to the bottom edge
 * of the viewport, and the "picture of the component" was a one-pixel strip.
 */
export function boxOf(selector: string, text: string): string {
  const wanted = JSON.stringify(text.toLowerCase());
  const css = JSON.stringify(selector);
  return `(() => {
    ${FIND}
    ${SELECTOR_FOR}
    const box = (el) => {
      intoView(el);
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return null;
      return {
        x: Math.round(r.left),
        y: Math.round(r.top),
        width: Math.round(r.width),
        height: Math.round(r.height),
      };
    };
    const asked = query(${css});
    if (asked.bad) return { invalid: ${css} };
    if (asked.el && visible(asked.el)) {
      const at = box(asked.el);
      if (at) return { ...at, selector: ${css}, label: labelFor(asked.el) };
    }
    const named = [
      ...document.querySelectorAll(
        ${JSON.stringify(`${CLICKABLE}, [data-testid], [data-test-id], section, article, header, footer, nav, main, form, table, figure`)},
      ),
    ].filter(visible);
    if (${wanted}) {
      const el =
        named.find((one) => labelFor(one).toLowerCase() === ${wanted}) ||
        named
          .filter((one) => labelFor(one).toLowerCase().includes(${wanted}))
          .sort((a, b) => labelFor(a).length - labelFor(b).length)[0];
      const at = el && box(el);
      if (at) return { ...at, selector: selectorFor(el), label: labelFor(el) };
    }
    return { options: named.map(labelFor).filter(Boolean).slice(0, 25) };
  })()`;
}

/**
 * A click, delivered where the page will see it. Answers with what it actually hit, or `null`
 * when nothing was at the point — which the caller must treat as a miss, not as a click.
 *
 * `elementFromPoint` rather than the element the finder located, because what is *on top* at
 * that coordinate is what a person's click would hit — an overlay, a consent banner, a label
 * wrapping the input. Clicking the thing underneath would be a click no person could have made.
 *
 * The full sequence, in order, with `bubbles` throughout: pointer events first for anything
 * listening to those, then mouse, then `click`. Focus moves the way a real click moves it — to
 * the nearest focusable thing, or off whatever had it — so the next `type` goes where the click
 * went rather than into a field that was focused three calls ago.
 */
export function dispatchClick(x: number, y: number): string {
  const at = `${Number(x) || 0}, ${Number(y) || 0}`;
  return `(() => {
    const el = document.elementFromPoint(${at});
    if (!el) return null;
    const point = { bubbles: true, cancelable: true, composed: true, clientX: ${Number(x) || 0}, clientY: ${Number(y) || 0}, button: 0, view: window };
    const pointer = { ...point, pointerId: 1, pointerType: "mouse", isPrimary: true };
    el.dispatchEvent(new PointerEvent("pointerdown", { ...pointer, buttons: 1 }));
    el.dispatchEvent(new MouseEvent("mousedown", { ...point, buttons: 1, detail: 1 }));
    const focusable = el.closest("a[href], button, input, select, textarea, summary, [tabindex], [contenteditable]");
    if (focusable && typeof focusable.focus === "function") focusable.focus();
    else if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
    el.dispatchEvent(new PointerEvent("pointerup", { ...pointer, buttons: 0 }));
    el.dispatchEvent(new MouseEvent("mouseup", { ...point, buttons: 0, detail: 1 }));
    el.dispatchEvent(new MouseEvent("click", { ...point, buttons: 0, detail: 1 }));
    return (el.innerText || el.getAttribute("aria-label") || el.value || "").replace(/\\s+/g, " ").trim().slice(0, 60) || el.tagName.toLowerCase();
  })()`;
}

/** What `focusField` answers with. `before` is the field's content before anything is typed, so
 *  the caller can tell a keystroke that landed from one that did not. */
/** How a test would find the same field again — see `focusField`. */
export type FieldBy = "label" | "placeholder" | "title" | "selector" | "focused";

export type Focused =
  | { name: string; kind: "text" | "select" | "rich"; before: string; by: FieldBy; key: string }
  | { fields: string[] }
  | { problem: "frame" | "unfocused"; focused: string };

/**
 * Find a field and put the caret in it, ready for text.
 *
 * **With `into`**, the field is found by the name a person would use for it — its `<label>`,
 * `aria-label`, `aria-labelledby`, placeholder, `name` or id — or by a selector, and whatever is
 * in it is selected so the text replaces it. Replace rather than append because that is what
 * "type X into Y" means, and because a call that is retried after a timeout must not leave the
 * text in the field twice.
 *
 * The label is the one that matters. `type(into: "Work email")` is the skill's own example and
 * it did not work: the finder read placeholders and `aria-label` but never the `<label for>` that
 * a plainly written form actually has, found nothing, and the text went into whatever happened
 * to be focused — with the answer reporting success.
 *
 * **Without `into`**, it is the focused element, with the caret where it is: a paste.
 *
 * Nothing is typed here. The text goes in from the main process through `insertText` — the IME
 * path, which fires `beforeinput` the way a keyboard does and which rich editors (ProseMirror,
 * Lexical, Slate) are built on — and `typed` then checks it landed.
 */
export function focusField(into: string): string {
  const wanted = JSON.stringify(into.replace(/\s+/g, " ").trim().toLowerCase());
  const css = JSON.stringify(into);
  return `(() => {
    ${FIND}
    const TEXTLESS = ["button", "submit", "reset", "checkbox", "radio", "file", "image", "hidden", "range", "color"];
    const editable = (el) =>
      !!el &&
      (el.isContentEditable ||
        el instanceof HTMLTextAreaElement ||
        el instanceof HTMLSelectElement ||
        (el instanceof HTMLInputElement && !TEXTLESS.includes(el.type))) &&
      !el.disabled &&
      !el.readOnly;
    /* Every name a field goes by, and how a Playwright test would find it by that name — so a
     * field Kith typed into by its label is one a saved test finds by its label, not by a CSS path
     * that the next restyle breaks. */
    const attr = (name, value) => "[" + name + '="' + CSS.escape(value) + '"]';
    const sourcesOf = (el) => {
      const out = [];
      const add = (name, by, key) => {
        // "Email *" and "Email:" are what a form says; "Email" is what anybody asking means.
        const clean = squash(name).replace(/[\\s:*]+$/, "");
        if (clean) out.push({ name: clean, by, key: key === undefined ? clean : key });
      };
      if (el.labels) for (const one of el.labels) add(one.innerText, "label");
      add(el.getAttribute("aria-label"), "label");
      const by = el.getAttribute("aria-labelledby");
      if (by) for (const id of by.split(/\\s+/)) add(document.getElementById(id)?.innerText, "label");
      add(el.getAttribute("placeholder"), "placeholder");
      for (const name of ["aria-placeholder", "data-placeholder", "name"]) {
        const value = el.getAttribute(name);
        if (value) add(value, "selector", attr(name, value));
      }
      add(el.title, "title");
      if (el.id) add(el.id, "selector", "#" + CSS.escape(el.id));
      return out;
    };
    const namesOf = (el) => sourcesOf(el).map((one) => one.name);
    const describe = (el) => namesOf(el)[0] || el.tagName.toLowerCase();

    let el = null;
    let how = { by: "focused", key: "" };
    if (${css}) {
      const asked = query(${css});
      const hit = asked.el;
      if (hit) {
        el = editable(hit)
          ? hit
          : editable(hit.control)
            ? hit.control
            : [...hit.querySelectorAll("input, textarea, select, [contenteditable]")].find(editable) || null;
        if (el) how = { by: "selector", key: ${css} };
      }
      if (!el) {
        const fields = [...document.querySelectorAll("input, textarea, select, [contenteditable]")].filter(
          (one) => editable(one) && visible(one) && !(one.parentElement && one.parentElement.isContentEditable),
        );
        const named = fields.map((one) => ({ one, sources: sourcesOf(one) }));
        const lower = (source) => source.name.toLowerCase();
        let source = null;
        let found = null;
        for (const exact of [true, false]) {
          for (const field of named) {
            source = field.sources.find((one) => (exact ? lower(one) === ${wanted} : lower(one).includes(${wanted})));
            if (source) {
              found = field;
              break;
            }
          }
          if (found) break;
        }
        if (!found || !source) return { fields: fields.map(describe).slice(0, 25) };
        el = found.one;
        how = { by: source.by, key: source.key };
      }
    } else {
      el = document.activeElement;
      if (el && el.tagName === "IFRAME") return { problem: "frame", focused: "" };
      if (!editable(el)) {
        return { problem: "unfocused", focused: el && el !== document.body ? describe(el) : "" };
      }
    }

    // The editing host, not a paragraph inside it: selecting "all of it" means all of the editor.
    while (el.isContentEditable && el.parentElement && el.parentElement.isContentEditable) el = el.parentElement;
    intoView(el);
    el.focus({ preventScroll: true });
    const selection = getSelection();
    if (${css}) {
      if (typeof el.select === "function") el.select();
      else if (el.isContentEditable) {
        const range = document.createRange();
        range.selectNodeContents(el);
        selection.removeAllRanges();
        selection.addRange(range);
      }
    } else if (el.isContentEditable && !(selection.rangeCount && el.contains(selection.anchorNode))) {
      const range = document.createRange();
      range.selectNodeContents(el);
      range.collapse(false);
      selection.removeAllRanges();
      selection.addRange(range);
    }
    const kind = el instanceof HTMLSelectElement ? "select" : el.isContentEditable ? "rich" : "text";
    return { name: describe(el), kind, before: el.isContentEditable ? el.innerText : el.value, ...how };
  })()`;
}

/**
 * After `insertText`: did anything arrive, and if not, put it there another way.
 *
 * The fallbacks are for a field `insertText` did not reach — an input the page swapped out on
 * focus, say. `execCommand("insertText")` first, because it still fires `input` with the right
 * `inputType`; the prototype's own `value` setter last, because that is the one React's value
 * tracking hooks into, where a plain `el.value =` would be reverted on the next render.
 *
 * `change` is dispatched for an input or a textarea because a real one fires it on blur, and a
 * good deal of validation listens for nothing else.
 */
export function typed(text: string, before: string, replace: boolean): string {
  const value = JSON.stringify(text);
  const was = JSON.stringify(before);
  return `(async () => {
    const el = document.activeElement;
    if (!el) return { landed: false, now: "" };
    const read = () => (el.isContentEditable ? el.innerText : el.value);
    // Unchanged is a miss — unless the field was being replaced with exactly what it held, which
    // is a success that looks like one. Typing it again there would leave it in the field twice.
    const arrived = () => !${value} || read() !== ${was} || (${replace} && read() === ${value});
    /* Waited for, briefly. \`insertText\` travels to the page on the input channel and this
     * script on another, and nothing orders the two: a check that ran first would see an empty
     * field, type the text again by the fallback, and then watch the first copy arrive. */
    for (let tries = 0; tries < 20 && !arrived(); tries += 1) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    let landed = arrived();
    if (!landed) {
      document.execCommand("insertText", false, ${value});
      landed = read() !== ${was};
    }
    if (!landed && (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      const next = ${replace ? value : `${was} + ${value}`};
      if (setter) setter.call(el, next);
      else el.value = next;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      landed = read() !== ${was};
    }
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return { landed, now: String(read()).slice(0, 200) };
  })()`;
}

/**
 * Choose an option in the focused `<select>`, by what it says or by its value.
 *
 * A select is a field somebody "types into" as far as the model is concerned — "put Canada in
 * Country" — and `insertText` does nothing to one. So it is chosen instead, through the
 * prototype's setter for the same React reason `typed` gives, and announced with `input` and
 * `change` the way a real choice is.
 */
export function chooseOption(text: string): string {
  const wanted = JSON.stringify(text.replace(/\s+/g, " ").trim().toLowerCase());
  return `(() => {
    const el = document.activeElement;
    if (!(el instanceof HTMLSelectElement)) return { chose: "", choices: [] };
    const options = [...el.options];
    const said = (one) => one.text.replace(/\\s+/g, " ").trim().toLowerCase();
    const pick =
      options.find((one) => said(one) === ${wanted} || one.value.toLowerCase() === ${wanted}) ||
      options.find((one) => said(one).includes(${wanted}));
    if (!pick) return { chose: "", choices: options.map((one) => one.text.trim()).slice(0, 40) };
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
    if (setter) setter.call(el, pick.value);
    else el.value = pick.value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { chose: pick.text.trim(), choices: [] };
  })()`;
}

/**
 * Is it on the page yet? For `wait`: text anywhere a person would read it, or an element a
 * selector finds that is actually drawn. Answers `"bad"` for a selector that does not parse, so
 * the caller can say so once rather than waiting out its whole timeout on a typo.
 */
export function seen(text: string, selector: string): string {
  const wanted = JSON.stringify(text.replace(/\s+/g, " ").trim().toLowerCase());
  const css = JSON.stringify(selector);
  return `(() => {
    if (${css}) {
      let el;
      try {
        el = document.querySelector(${css});
      } catch (_) {
        return "bad";
      }
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
    }
    const page = (document.body ? document.body.innerText : "").replace(/\\s+/g, " ").toLowerCase();
    return page.includes(${wanted});
  })()`;
}

/**
 * The keys a page may be sent, and what each one actually is.
 *
 * A closed table rather than a passthrough, because a page that checks `event.key` against a
 * name somebody guessed is a form that will not submit. These are the keys that *mean*
 * something; text goes through `type`.
 */
export const KEYS: Record<string, { key: string; code: string; keyCode: number }> = {
  Enter: { key: "Enter", code: "Enter", keyCode: 13 },
  Tab: { key: "Tab", code: "Tab", keyCode: 9 },
  Escape: { key: "Escape", code: "Escape", keyCode: 27 },
  Backspace: { key: "Backspace", code: "Backspace", keyCode: 8 },
  ArrowDown: { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
  ArrowUp: { key: "ArrowUp", code: "ArrowUp", keyCode: 38 },
};

/**
 * A key that means something — dispatched, and then **its default carried out**.
 *
 * A dispatched `KeyboardEvent` is `isTrusted: false`, and the browser performs no default action
 * for an untrusted key: Tab does not move focus, Backspace deletes nothing, Enter on a focused
 * button does not press it. The page's own handlers do see the event, so anything a page does
 * for itself still happens — but everything the *browser* would have done did not, and the
 * answer said the key had been pressed. So each default is done here explicitly, and only when
 * the page did not `preventDefault` it: a page that handles Enter itself has already done what it
 * meant to, and submitting on top of that would send the form twice.
 *
 * Rich editors get a `beforeinput` first, the event they are built to listen for, and the edit is
 * only made here if they did not take it.
 *
 * Answers with `did` — a few words on what happened — so the model can tell "focused Password"
 * from a key that reached the page and changed nothing.
 */
export function pressKey(named: { key: string; code: string; keyCode: number }): string {
  const key = JSON.stringify(named.key);
  const code = JSON.stringify(named.code);
  return `(() => {
    const el = document.activeElement || document.body;
    const describe = (one) =>
      (one.labels && one.labels[0] && one.labels[0].innerText.trim()) ||
      one.getAttribute("aria-label") ||
      one.getAttribute("placeholder") ||
      (one.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 40) ||
      one.tagName.toLowerCase();
    const shape = { key: ${key}, code: ${code}, keyCode: ${named.keyCode}, which: ${named.keyCode}, bubbles: true, cancelable: true, composed: true };
    const proceed = el.dispatchEvent(new KeyboardEvent("keydown", shape));
    if (proceed && ${key} === "Enter") el.dispatchEvent(new KeyboardEvent("keypress", { ...shape, charCode: 13 }));
    const textual = el instanceof HTMLTextAreaElement || (el instanceof HTMLInputElement && !["button", "submit", "reset", "checkbox", "radio", "file", "image", "hidden", "range", "color"].includes(el.type));
    const rich = el.isContentEditable;
    const edit = (inputType, command, argument) => {
      if (rich && !el.dispatchEvent(new InputEvent("beforeinput", { inputType, bubbles: true, cancelable: true, composed: true }))) return true;
      return document.execCommand(command, false, argument);
    };
    let did = "";
    if (!proceed) {
      did = "the page handled it";
    } else if (${key} === "Enter") {
      if (rich) did = edit("insertParagraph", "insertParagraph") ? "started a new line" : "";
      else if (el instanceof HTMLTextAreaElement) did = edit("insertLineBreak", "insertText", "\\n") ? "started a new line" : "";
      else if (textual && el.form) {
        el.form.requestSubmit();
        did = "submitted the form";
      } else if (el.matches("button, a[href], summary, [role=button], [role=link], input[type=submit], input[type=button], input[type=reset], input[type=image]")) {
        el.click();
        did = "pressed " + describe(el);
      }
    } else if (${key} === "Tab") {
      const tabbable = [...document.querySelectorAll("a[href], area[href], button, input, select, textarea, iframe, summary, [tabindex], [contenteditable]")].filter((one) => {
        if (one.tabIndex < 0 || one.disabled || (one instanceof HTMLInputElement && one.type === "hidden")) return false;
        const r = one.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && getComputedStyle(one).visibility !== "hidden";
      });
      // Positive tabindex first, in its own order; then everything else in document order.
      const ordered = [
        ...tabbable.filter((one) => one.tabIndex > 0).sort((a, b) => a.tabIndex - b.tabIndex),
        ...tabbable.filter((one) => one.tabIndex === 0),
      ];
      let at = ordered.indexOf(el);
      if (at < 0) at = ordered.findIndex((one) => el.compareDocumentPosition(one) & Node.DOCUMENT_POSITION_FOLLOWING) - 1;
      const next = ordered[at + 1] || ordered[0];
      if (next && next !== el) {
        next.focus();
        did = "moved focus to " + describe(next) + (ordered[at + 1] ? "" : " (back at the top)");
      }
    } else if (${key} === "Backspace") {
      if (textual || rich) did = edit("deleteContentBackward", "delete") ? "deleted" : "";
    } else if (${key} === "ArrowDown" || ${key} === "ArrowUp") {
      const step = ${key} === "ArrowDown" ? 1 : -1;
      if (el instanceof HTMLSelectElement) {
        const index = Math.max(0, Math.min(el.options.length - 1, el.selectedIndex + step));
        if (index !== el.selectedIndex) {
          el.selectedIndex = index;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
          did = "chose " + el.options[index].text.trim();
        }
      } else if (rich) {
        getSelection().modify("move", step > 0 ? "forward" : "backward", "line");
        did = "moved the caret";
      } else if (!textual) {
        const before = scrollY;
        window.scrollBy({ top: 40 * step, behavior: "instant" });
        if (scrollY !== before) did = "scrolled";
      }
    }
    el.dispatchEvent(new KeyboardEvent("keyup", shape));
    return { did, on: el === document.body ? "" : describe(el) };
  })()`;
}

/**
 * Scroll whatever actually scrolls.
 *
 * `window.scrollBy` alone did nothing on the pages people most want scrolled: an app whose
 * document is exactly the height of the window and whose content scrolls in a panel inside it
 * — a mail client, a dashboard, a chat. So the window is used when the document is taller than
 * the viewport, and otherwise the scrollable element under the middle of the viewport, and
 * failing that the largest one on the page.
 *
 * Answers with how far it actually moved, so reaching the bottom is a `0` rather than a scroll
 * that claims to have happened.
 */
export function scrollPage(by: number): string {
  const distance = Number(by) || 0;
  return `(() => {
    const scrolls = (el) =>
      el.scrollHeight > el.clientHeight + 1 && /(auto|scroll|overlay)/.test(getComputedStyle(el).overflowY);
    const root = document.scrollingElement || document.documentElement;
    // Taller than the window *and* allowed to scroll: an app shell sets \`overflow: hidden\` on the
    // body precisely so that the panel inside it is the thing that scrolls.
    const shut = /hidden|clip/.test(getComputedStyle(document.body).overflowY + getComputedStyle(root).overflowY);
    if (root.scrollHeight > innerHeight + 1 && !shut) {
      const before = scrollY;
      window.scrollBy({ top: ${distance}, behavior: "instant" });
      return { moved: Math.round(scrollY - before), where: "page" };
    }
    let el = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
    while (el && !scrolls(el)) el = el.parentElement;
    if (!el) {
      let area = 0;
      for (const one of document.querySelectorAll("body *")) {
        if (one.scrollHeight <= one.clientHeight + 1 || !scrolls(one)) continue;
        const r = one.getBoundingClientRect();
        if (r.width * r.height > area) {
          area = r.width * r.height;
          el = one;
        }
      }
    }
    if (!el) return { moved: 0, where: "" };
    const before = el.scrollTop;
    el.scrollBy({ top: ${distance}, behavior: "instant" });
    const where = (el.getAttribute("aria-label") || el.id || el.tagName.toLowerCase());
    return { moved: Math.round(el.scrollTop - before), where };
  })()`;
}

/**
 * The inspector, as a script the page runs.
 *
 * Everything drawn is `pointer-events: none` and `position: fixed`, so it never changes what is
 * under the pointer and never contributes to the page's own layout — an overlay that reflows the
 * thing you are trying to measure would be the one unforgivable version of this.
 *
 * **Every half of a click is swallowed, not just the click.** Picking the "Delete account"
 * button must never also press it, and the capture used to stop only `click` — so anything that
 * acts on `pointerdown` or `mousedown` (a menu trigger, a drag handle, half the custom buttons on
 * the web) went off while it was being pointed at. Frames get `pointer-events: none` for the
 * same reason: a click over an iframe lands in the iframe's own document, where no listener of
 * this one can stop it, so the frame is made transparent to the pointer and its container is
 * what gets picked.
 *
 * `window.__kithPick` is the handle the outside world has on it: starting a second pick stops
 * the first, and `unpick` uses it when the pane goes away. A picker with no way to be stopped
 * from outside is an outline following the pointer on a page nobody is picking from.
 */
export function picker(): string {
  return `(() => {
    ${SELECTOR_FOR}
    if (window.__kithPick) window.__kithPick.stop(null);
    return new Promise((resolve) => {
      const chrome = [];
      const make = (css) => {
        const el = document.createElement("div");
        el.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;" + css;
        document.documentElement.appendChild(el);
        chrome.push(el);
        return el;
      };
      const frames = document.createElement("style");
      frames.textContent = "iframe, embed, object { pointer-events: none !important; }";
      document.documentElement.appendChild(frames);
      chrome.push(frames);
      const outline = make(
        "border:2px solid #4c8dff;background:rgba(76,141,255,0.13);border-radius:3px;box-shadow:0 0 0 1px rgba(0,0,0,0.28);left:0;top:0;width:0;height:0",
      );
      const tip = make(
        "font:11px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;background:#11161f;color:#e9eef7;padding:2px 7px;border-radius:5px;white-space:nowrap;max-width:70vw;overflow:hidden;text-overflow:ellipsis;left:0;top:0;opacity:0",
      );
      const hint = make(
        "left:50%;bottom:18px;transform:translateX(-50%);font:12px/1.6 system-ui,-apple-system,sans-serif;background:#11161f;color:#e9eef7;padding:6px 13px;border-radius:999px;opacity:.95",
      );
      hint.textContent = "Click a component to send it to the chat  ·  Esc to cancel";

      let at = null;
      const draw = (el) => {
        at = el;
        if (!el || !el.getBoundingClientRect) { outline.style.width = "0"; tip.style.opacity = "0"; return; }
        const r = el.getBoundingClientRect();
        outline.style.left = r.left + "px";
        outline.style.top = r.top + "px";
        outline.style.width = Math.max(0, r.width - 4) + "px";
        outline.style.height = Math.max(0, r.height - 4) + "px";
        tip.textContent = (selectorFor(el) || el.tagName.toLowerCase()) +
          "  " + Math.round(r.width) + "×" + Math.round(r.height);
        tip.style.opacity = "1";
        // Above the element, or below it when it is against the top of the window.
        tip.style.left = Math.max(4, Math.min(r.left, window.innerWidth - 240)) + "px";
        tip.style.top = (r.top > 24 ? r.top - 22 : Math.min(r.bottom + 6, window.innerHeight - 24)) + "px";
      };

      /* What is under the pointer — counting frames, which the pointer can no longer see. A frame
       * under the point is the answer unless something drawn over it is: its own container
       * containing it is not "over" it. */
      const under = (x, y) => {
        const hit = document.elementFromPoint(x, y);
        const frames = [...document.querySelectorAll("iframe, embed, object")].filter((one) => {
          const r = one.getBoundingClientRect();
          return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
        });
        const frame = frames[frames.length - 1];
        return frame && (!hit || hit.contains(frame)) ? frame : hit;
      };

      let last = { x: -1, y: -1 };
      const onMove = (event) => {
        last = { x: event.clientX, y: event.clientY };
        draw(under(event.clientX, event.clientY));
      };
      const onScroll = () => {
        if (last.x >= 0) draw(under(last.x, last.y));
      };
      const onKey = (event) => {
        if (event.key === "Escape") { event.preventDefault(); stop(null); }
      };
      // Three ways, in the capture phase, on every event a click is made of.
      const swallow = (event) => {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
      };
      const SWALLOWED = ["pointerdown", "mousedown", "pointerup", "mouseup", "auxclick", "dblclick", "contextmenu"];
      const onClick = (event) => {
        swallow(event);
        const el = at || under(event.clientX, event.clientY);
        if (!el || !el.getBoundingClientRect) return stop(null);
        const r = el.getBoundingClientRect();
        stop({
          x: Math.round(r.left),
          y: Math.round(r.top),
          width: Math.round(r.width),
          height: Math.round(r.height),
          selector: selectorFor(el),
          label: labelFor(el),
          tag: el.tagName.toLowerCase(),
        });
      };

      let done = false;
      function stop(answer) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        window.removeEventListener("mousemove", onMove, true);
        window.removeEventListener("scroll", onScroll, true);
        window.removeEventListener("keydown", onKey, true);
        window.removeEventListener("click", onClick, true);
        for (const type of SWALLOWED) window.removeEventListener(type, swallow, true);
        for (const el of chrome) el.remove();
        if (window.__kithPick && window.__kithPick.stop === stop) delete window.__kithPick;
        /* The outline has to be off the page before the capture is taken, and removing an
         * element does not repaint before this promise's continuation runs in the main process.
         * One frame is the difference between a picture of a component and a picture of a blue
         * rectangle.
         *
         * The timer beside it is not belt and braces. A hidden view produces no frames at all,
         * so if the tab is switched away from mid-pick the callbacks never run — and the promise
         * this resolves is one the main process is awaiting, which would hang the call for ever
         * rather than answering "cancelled". */
        let settled = false;
        const answered = () => {
          if (settled) return;
          settled = true;
          resolve(answer);
        };
        requestAnimationFrame(() => requestAnimationFrame(answered));
        setTimeout(answered, 120);
      }

      // A picker nobody finishes is an overlay on a page somebody walked away from.
      const timer = setTimeout(() => stop(null), 60000);
      window.__kithPick = { stop };
      window.addEventListener("mousemove", onMove, true);
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("keydown", onKey, true);
      window.addEventListener("click", onClick, true);
      for (const type of SWALLOWED) window.addEventListener(type, swallow, true);
    });
  })()`;
}
