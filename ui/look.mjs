import { chromium } from "playwright";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
const errors = [];
page.on("pageerror", (e) => errors.push("pageerror: " + e.message.split("\n")[0]));
await page.goto("http://127.0.0.1:8611", { waitUntil: "networkidle" });
await page.waitForTimeout(2000);

// Open a conversation that has history — the case the crash was reported in.
const first = page.locator('aside button:has-text("msg")').first();
if (await first.count()) {
  await first.click().catch(() => {});
  await page.waitForTimeout(2500);
}
console.log("after opening a conversation, errors:", errors.length ? errors[0] : "none");

const box = page.locator('textarea[aria-label="Message input"]');
await box.click();
await box.type("/", { delay: 80 });
await page.waitForTimeout(900);

const menu = page.locator('[data-radix-popper-content-wrapper], div:has-text("↑↓ move")').last();
const geo = await page.evaluate(() => {
  const ta = document.querySelector('textarea[aria-label="Message input"]');
  const shell = ta?.closest('[data-slot="aui_composer-shell"]');
  const hint = [...document.querySelectorAll("p")].find((p) => p.textContent?.includes("↑↓ move"));
  const menu = hint?.closest("div")?.parentElement;
  const r = (el) => (el ? el.getBoundingClientRect() : null);
  const s = r(shell), m = r(menu);
  return {
    menuVisible: !!m && m.height > 0,
    items: [...document.querySelectorAll("button")].filter((b) => /^\/(fold|stop)/.test(b.textContent ?? "")).length,
    gap: s && m ? Math.round(s.top - m.bottom) : null,
    sameWidth: s && m ? Math.abs(Math.round(s.width - m.width)) : null,
    menuHeight: m ? Math.round(m.height) : null,
  };
});
console.log("geometry:", JSON.stringify(geo));
console.log("errors:", errors.length ? errors.slice(0, 2) : "none");
await browser.close();
