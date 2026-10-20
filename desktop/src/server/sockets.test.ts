import { describe, expect, it } from "vitest";

import { tolerateDroppedSockets } from "./sockets";

function failingWith(code: string) {
  const prototype = {
    setTypeOfService(this: unknown, _tos: number): unknown {
      throw Object.assign(new Error(`setTypeOfService ${code}`), { code });
    },
  };
  tolerateDroppedSockets(prototype);
  return prototype;
}

describe("a socket the server dropped", () => {
  it("does not throw EINVAL out of a socket callback", () => {
    const socket = failingWith("EINVAL");
    expect(() => socket.setTypeOfService(0)).not.toThrow();
  });

  it("still throws anything else", () => {
    const socket = failingWith("EBADF");
    expect(() => socket.setTypeOfService(0)).toThrow("EBADF");
  });

  it("is wrapped once however often it is installed", () => {
    const prototype = failingWith("EINVAL");
    const once = prototype.setTypeOfService;
    tolerateDroppedSockets(prototype);
    expect(prototype.setTypeOfService).toBe(once);
  });
});
