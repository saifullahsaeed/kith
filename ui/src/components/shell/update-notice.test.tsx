import { describe, expect, it } from "vitest";

import { version } from "@/components/shell/update-notice";
import type { UpdateState } from "@/lib/backend";

const state = (over: Partial<UpdateState> = {}): UpdateState => ({
  current: "0.5.0",
  latest: "0.5.0",
  newer: false,
  packaged: true,
  page: "",
  download: "",
  notes: "",
  publishedAt: "",
  checkedAt: "",
  error: "",
  ...over,
});

/**
 * The line under the settings nav, which is the only place the running version is ever shown.
 *
 * Its one hard rule is that "there is nothing to check" and "you are up to date" must not read
 * the same. A checkout has no installed version, so claiming it is current would be a statement
 * about something that does not exist — and would put a dmg in front of someone running from
 * source, which is worse than saying nothing.
 */
describe("what the footer says about this copy", () => {
  it("names the version and says it is current", () => {
    expect(version(state())).toBe("Kith 0.5.0 · latest");
  });

  it("drops 'latest' the moment it is not", () => {
    expect(version(state({ newer: true, latest: "0.6.0" }))).toBe("Kith 0.5.0");
  });

  it("says where it is running from rather than claiming to be up to date", () => {
    expect(version(state({ packaged: false, current: "" }))).toBe("running from source");
  });

  it("says nothing at all before the first answer arrives", () => {
    // Not "Kith undefined", and not an optimistic "latest" either.
    expect(version(undefined)).toBe("");
  });

  it("does not claim to be current when nothing managed to look", () => {
    // The failure that must not look like success. With no `latest` remembered, "latest" would
    // be a claim nothing checked — and the footer is saying the check failed right underneath.
    const failed = state({ latest: "", error: "ConnectionError: no route to host" });
    expect(version(failed)).toBe("Kith 0.5.0");
  });

  it("still says latest when a failed look had a good answer to fall back on", () => {
    // An update found yesterday does not stop being known because the wifi dropped, and being
    // on the newest version we ever saw is a fact worth keeping.
    const stale = state({ latest: "0.5.0", error: "ConnectionError: no route to host" });
    expect(version(stale)).toBe("Kith 0.5.0 · latest");
  });
});
