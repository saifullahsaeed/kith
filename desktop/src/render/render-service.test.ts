import { describe, expect, it } from "vitest";

import { isLocalAddress } from "./render-service";

/**
 * The render window's refusal of anything local.
 *
 * It renders addresses a *model* chose, following links it found on the web, so every spelling
 * of this machine and its network has to be refused — including IPv6's, which the IPv4 checks
 * never saw: `[::ffff:127.0.0.1]` is 127.0.0.1, and it reached Kith's own server.
 */
describe("what the render window will not open", () => {
  it("refuses loopback and private addresses however they are written", () => {
    for (const address of [
      "http://127.0.0.1:8611/",
      "http://localhost:8611/",
      "http://localhost.:8611/",
      "http://[::1]:8611/",
      "http://[::]:8611/",
      "http://[::ffff:127.0.0.1]:8611/",
      "http://[::ffff:10.0.0.5]/",
      "http://[::ffff:169.254.169.254]/",
      "http://[fd00::1]/",
      "http://[fe80::1]/",
      "http://2130706433/",
      "http://0.0.0.0/",
      "http://192.168.1.1/",
    ]) {
      expect(isLocalAddress(address), address).toBe(true);
    }
  });

  it("lets the web through", () => {
    for (const address of ["https://example.com/", "https://93.184.216.34/", "https://[2606:4700::6810:84e5]/"]) {
      expect(isLocalAddress(address), address).toBe(false);
    }
  });
});
