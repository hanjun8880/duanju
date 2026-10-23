import { describe, expect, it } from "vitest";
import { toggleLock } from "./lock";

describe("toggleLock", () => {
  it("locks up to three in click order", () => {
    expect(toggleLock([], "a")).toEqual(["a"]);
    expect(toggleLock(["a", "b"], "c")).toEqual(["a", "b", "c"]);
  });
  it("refuses a fourth", () => {
    expect(toggleLock(["a", "b", "c"], "d")).toBeNull();
  });
  it("unlocks a locked one", () => {
    expect(toggleLock(["a", "b", "c"], "b")).toEqual(["a", "c"]);
  });
});
