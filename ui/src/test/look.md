# Looking at it

`ui/look.mjs` loads the running app in headless Chromium, drives it, and reports what it finds.

    cd ui && node look.mjs

It exists because of a specific failure. Five interface changes shipped broken in one day, and
three of them I reported as working on the evidence that `tsc` passed — which says the types
agree and nothing about whether a component mounts. A context-provider error throws at mount, in
a browser, and there was no browser here. So the testing was being done by the person using the
app, and reported back in anger, which is a review process nobody agreed to.

Two things it gives that vitest cannot:

**Whether it throws.** `page.on("pageerror")` catches what an error boundary swallows into "The
conversation stopped working". jsdom cannot run the real render.

**Geometry.** Bounding boxes answer the questions that had been going back and forth in words:
is the menu attached or floating, is it the composer's width, is there a gap. The slash menu
reported `gap: -94, sameWidth: 0` — flush, exact width — which is a fact rather than an opinion
about a screenshot.

Prefer measuring to screenshotting. A number in the terminal beats an image: it needs no
downscaling, it survives being quoted in a commit message, and it does not depend on anyone's
eyes. Screenshots are for "does this look right", which is still a question for a person.
