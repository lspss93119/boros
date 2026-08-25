import { describe, expect, it } from "vitest";
import { isLocalMode } from "./mode";

describe("dashboard mode", () => {
  it("enables live mode only for localhost and 127.0.0.1", () => {
    expect(isLocalMode("localhost")).toBe(true);
    expect(isLocalMode("127.0.0.1")).toBe(true);
    expect(isLocalMode("0.0.0.0")).toBe(false);
    expect(isLocalMode("192.168.1.10")).toBe(false);
    expect(isLocalMode("boros.pages.dev")).toBe(false);
  });
});
