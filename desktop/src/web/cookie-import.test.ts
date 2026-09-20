import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { domainMatch, epochOf, hostnameOf, namesAt, sameSiteOf } from "./cookie-import";

/**
 * Copying a login out of another browser, and the three ways that goes quietly wrong.
 *
 * The import itself needs a real keychain, a real Chrome profile and a real Electron session, so
 * none of it runs here. What does run is every place a wrong answer would be *plausible* — a
 * domain filter that matches one character too few, a clock read against the wrong epoch, a
 * cross-site cookie that arrives unable to do the thing it was copied for. All three produce an
 * import that reports a cheerful number and leaves you still logged out.
 */

describe("which cookies count as the domain's", () => {
  it("matches the domain and its subdomains", () => {
    const sql = domainMatch("host_key", "example.com");
    expect(sql).toContain("host_key = 'example.com'");
    expect(sql).toContain("host_key = '.example.com'");
    expect(sql).toContain("host_key LIKE '%.example.com'");
  });

  it("never matches a domain that merely ends with it", () => {
    /* The bug this exists to prevent: `LIKE '%example.com'` also matches `notexample.com`, so a
     * suffix filter written the obvious way imports a stranger's cookies alongside the ones that
     * were asked for. The dot in the pattern is the whole difference. */
    const sql = domainMatch("host_key", "example.com");
    expect(sql).not.toMatch(/LIKE '%example\.com'/);
  });
});

describe("what counts as a domain at all", () => {
  it("takes a hostname, a URL, or a leading dot", () => {
    expect(hostnameOf("example.com")).toBe("example.com");
    expect(hostnameOf("  Example.COM ")).toBe("example.com");
    expect(hostnameOf(".example.com")).toBe("example.com");
    expect(hostnameOf("https://app.example.com/dashboard?x=1")).toBe("app.example.com");
    expect(hostnameOf("app.example.com/dashboard")).toBe("app.example.com");
  });

  it("refuses anything that is not one", () => {
    /* This is the only value that reaches SQL, and it is interpolated rather than bound — see
     * the module comment for why there is no binding available. The pattern is what makes that
     * safe, so a quote, a wildcard or a comment must not survive it. */
    for (const bad of [
      "",
      "localhost",
      "example",
      "example.com'; DROP TABLE cookies; --",
      "%.com",
      "exa mple.com",
      "-example.com",
      "example..com",
      "http://[::1]/",
    ]) {
      expect(hostnameOf(bad)).toBe("");
    }
  });
});

describe("the two clocks", () => {
  it("reads Chromium's microseconds since 1601", () => {
    // 31 March 2024, as Chrome stores it. Read as seconds-since-1970 the same number lands
    // some 400,000 years out, and the cookie is imported with an expiry nothing ever reaches —
    // which looks like a successful import right until the session turns out not to work.
    expect(epochOf({ raw_expires: 13_356_326_400_000_000 })).toBe(1_711_852_800);
  });

  it("reads Firefox's seconds since 1970 unchanged", () => {
    expect(epochOf({ raw_expires: 1_711_852_800 })).toBe(1_711_852_800);
  });

  it("leaves a session cookie without an expiry", () => {
    // Zero has to survive as zero: `importCookies` turns it into an omitted `expirationDate`,
    // which is what makes the copy a session cookie in the destination too.
    expect(epochOf({ raw_expires: 0 })).toBe(0);
    expect(epochOf({})).toBe(0);
  });
});

describe("same-site, which decides whether the copy is any use", () => {
  it("carries strict and lax across", () => {
    expect(sameSiteOf(2, true)).toBe("strict");
    expect(sameSiteOf(1, false)).toBe("lax");
  });

  it("only promises no_restriction on a secure cookie", () => {
    // Chromium rejects `SameSite=None` without `Secure`, and a rejected `set` is a cookie that
    // silently never arrives — so an insecure one is downgraded rather than refused.
    expect(sameSiteOf(0, true)).toBe("no_restriction");
    expect(sameSiteOf(0, false)).toBe("unspecified");
  });

  it("leaves an unspecified one unspecified", () => {
    expect(sameSiteOf(-1, true)).toBe("unspecified");
  });
});

describe("what the model cannot reach", () => {
  /* The argument in `cookie-import.ts` rests on this and nothing else: the import is something a
   * person does, from Kith's own chrome, having named a site. A `/browse` case for it would make
   * it a call — and `/browse` is the model's door. Asserted against the source because there is
   * no runtime in which to ask the question, and because the failure is silent: an added case
   * looks like a feature until you notice who can now use it. */
  const service = readFileSync(join(__dirname, "../render/render-service.ts"), "utf8");

  it("has no /browse case for the picker or the cookie import", () => {
    expect(service).not.toContain('case "pick"');
    expect(service).not.toContain('case "unpick"');
    expect(service).not.toContain("cookie-import");
    expect(service).not.toContain("cookie-sources");
  });

  it("still has the ones it should", () => {
    // The negative above would also pass if somebody deleted the whole switch.
    expect(service).toContain('case "look"');
    expect(service).toContain('case "resize"');
    // `look` is the door to a cropped picture as well as to a whole-page one — the model's half
    // of the picker, without the pointing.
    expect(service).toContain("views.snap(at");
  });
});

describe("naming the profiles", () => {
  it("prefers what the person called it over the folder it lives in", () => {
    // The whole point: "Google Chrome (4 profiles)" said how many there were and not which one
    // you are signed in on, which is the only part that matters when one is work and one is
    // personal.
    const root = mkdtempSync(join(tmpdir(), "kith-profiles-"));
    mkdirSync(join(root, "Default", "Network"), { recursive: true });
    mkdirSync(join(root, "Profile 3", "Network"), { recursive: true });
    writeFileSync(join(root, "Default", "Network", "Cookies"), "");
    writeFileSync(join(root, "Profile 3", "Network", "Cookies"), "");
    writeFileSync(
      join(root, "Local State"),
      JSON.stringify({ profile: { info_cache: { Default: { name: "Personal" }, "Profile 3": { name: "Saif (Work)" } } } }),
    );

    const named = namesAt("chromium", root);
    expect(named.Default).toBe("Personal");
    expect(named["Profile 3"]).toBe("Saif (Work)");
  });

  it("falls back to the folder when there is no Local State", () => {
    // A browser with an unreadable Local State must still be choosable — a missing name is not
    // a missing profile.
    const root = mkdtempSync(join(tmpdir(), "kith-profiles-"));
    expect(namesAt("chromium", root)).toEqual({});
  });

  it("reads Firefox's profiles.ini", () => {
    const home = mkdtempSync(join(tmpdir(), "kith-ff-"));
    const root = join(home, "Profiles");
    mkdirSync(root, { recursive: true });
    writeFileSync(
      join(home, "profiles.ini"),
      "[Profile0]\nName=default-release\nPath=Profiles/abc.default-release\n\n[Profile1]\nName=Work\nPath=Profiles/xyz.work\n",
    );

    const named = namesAt("firefox", root);
    expect(named["abc.default-release"]).toBe("default-release");
    expect(named["xyz.work"]).toBe("Work");
  });
});
