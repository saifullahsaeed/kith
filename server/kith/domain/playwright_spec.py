"""A Playwright test, written from what Kith did in the Browser tab.

The desktop app records each act the model takes in a tab — in the shape a test needs to take it
again: a click keeps the element's role and name, a field keeps how it was found (its label, its
placeholder, a selector). This turns that recording into an ordinary `@playwright/test` spec, the
format every Playwright project already runs, so a check Kith did by hand once becomes a check
the project runs on every commit without him.

Pure: steps in, source and warnings out. Writing the file is the caller's, through the same
permission-checked write every other tool uses.

**It repeats what he did; it only asserts what he waited for.** A recording has no idea what
"worked" means except where the model said so with `wait` — so a flow with no `wait` in it is a
test that passes as long as nothing throws, and the notes say that rather than let it look like
coverage.
"""

from __future__ import annotations

import json
from datetime import date


def _js(value: object) -> str:
    """A JavaScript string literal. JSON's escaping is a subset of JavaScript's."""
    return json.dumps(str(value), ensure_ascii=False)


def _field(by: str, key: str) -> str | None:
    """The locator for a field, found the way Kith found it. None for "whatever had focus"."""
    if by == "label":
        return f"page.getByLabel({_js(key)})"
    if by == "placeholder":
        return f"page.getByPlaceholder({_js(key)})"
    if by == "title":
        return f"page.getByTitle({_js(key)})"
    if by == "selector":
        return f"page.locator({_js(key)})"
    return None


def write(
    name: str, steps: list[dict], *, signed_in: bool, when: date | None = None
) -> tuple[str, list[str]]:
    """The spec's source, and what anybody keeping it should know about it."""
    body: list[str] = []
    notes: list[str] = []
    checks = 0

    for step in steps:
        kind = step.get("kind")
        if kind == "goto":
            body.append(f"await page.goto({_js(step.get('url', ''))});")
        elif kind == "click":
            role, label = str(step.get("role") or ""), str(step.get("name") or "")
            if step.get("point"):
                body.append(
                    f"// Clicked at {step['point']} — a point on the screen, which no test can find again."
                )
                notes.append("One click was at a screen position rather than on a named element; replace it.")
            elif step.get("selector"):
                body.append(f"await page.locator({_js(step['selector'])}).first().click();")
            elif role and label:
                body.append(f"await page.getByRole({_js(role)}, {{ name: {_js(label)} }}).first().click();")
            elif label:
                body.append(f"await page.getByText({_js(label)}).first().click();")
            else:
                body.append(
                    "// A click something else on the page took — see what covered it before keeping this."
                )
                notes.append("One click landed on something covering the element meant; check that step.")
            if step.get("openedTab"):
                body.append("// That opened a new tab. What was done there is that tab's own recording.")
        elif kind == "fill":
            locator = _field(str(step.get("by") or ""), str(step.get("key") or ""))
            text = _js(step.get("text", ""))
            body.append(
                f"await {locator}.fill({text});" if locator else f"await page.keyboard.insertText({text});"
            )
        elif kind == "select":
            locator = (
                _field(str(step.get("by") or ""), str(step.get("key") or ""))
                or "page.locator('select:focus')"
            )
            body.append(f"await {locator}.selectOption({{ label: {_js(step.get('option', ''))} }});")
        elif kind == "press":
            body.append(f"await page.keyboard.press({_js(step.get('key', ''))});")
        elif kind == "wait":
            checks += 1
            gone = bool(step.get("gone"))
            if step.get("url"):
                body.append(f"await page.waitForURL((url) => url.href.includes({_js(step['url'])}));")
            else:
                target = (
                    f"page.locator({_js(step['selector'])})"
                    if step.get("selector")
                    else f"page.getByText({_js(step.get('text', ''))})"
                )
                body.append(f"await expect({target}.first()).{'toBeHidden' if gone else 'toBeVisible'}();")

    if not checks:
        body.append("// Nothing here checks an outcome yet: add what should appear once this has worked.")
        notes.append(
            "Nothing in it checks an outcome — it passes as long as no step throws. `wait` for what "
            "should appear before saving, and that becomes the test's assertion."
        )
    if signed_in:
        notes.append(
            "It was recorded in the Browser's own session, where you may be signed in. A Playwright "
            "test starts signed out, so it may need to sign in first — a private tab records it that way."
        )

    uses_expect = any("expect(" in line for line in body)
    imports = "test, expect" if uses_expect else "test"
    stamp = (when or date.today()).isoformat()
    source = "\n".join(
        [
            f'import {{ {imports} }} from "@playwright/test";',
            "",
            f"// Written by Kith on {stamp} from what it did in the Browser tab. It repeats those steps,",
            "// and it only checks what was waited for — read it before trusting it.",
            f"test({_js(name)}, async ({{ page }}) => {{",
            *(f"  {line}" for line in body),
            "});",
            "",
        ]
    )
    return source, notes
