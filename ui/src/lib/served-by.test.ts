import { describe, expect, it } from "vitest";

import { servedBy } from "./served-by";

describe("servedBy", () => {
  it("names the tier when the host says it was not the ordinary one", () => {
    expect(servedBy("OpenAI", "flex")).toBe("OpenAI · flex");
  });

  it("leaves the ordinary tier unsaid", () => {
    expect(servedBy("OpenAI", "default")).toBe("OpenAI");
    expect(servedBy("Novita", undefined)).toBe("Novita");
  });

  it("is nothing without a host", () => {
    expect(servedBy("", "flex")).toBeUndefined();
    expect(servedBy(null, null)).toBeUndefined();
  });
});
