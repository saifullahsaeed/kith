import { describe, expect, it } from "vitest";

import { splitWorkflowPrefix } from "./workflow-prefix";

describe("reading the /workflow prefix", () => {
  it("takes the prefix off and keeps the request", () => {
    expect(splitWorkflowPrefix("/workflow port the rag package")).toEqual({
      workflow: true,
      rest: "port the rag package",
    });
  });

  it("matches the server: alone, indented, or any case", () => {
    expect(splitWorkflowPrefix("  /workflow").workflow).toBe(true);
    expect(splitWorkflowPrefix("/Workflow go").workflow).toBe(true);
  });

  it("leaves anything else alone", () => {
    for (const text of ["/workflows", "/workflowish x", "use a /workflow", ""]) {
      expect(splitWorkflowPrefix(text)).toEqual({ workflow: false, rest: text });
    }
  });
});
