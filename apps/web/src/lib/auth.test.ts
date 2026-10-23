import { describe, expect, it } from "vitest";
import { withAuth } from "./api";

describe("withAuth", () => {
  it("adds the bearer only when a token exists", () => {
    expect(withAuth({ "content-type": "application/json" }, "abc")).toEqual({
      "content-type": "application/json", authorization: "Bearer abc",
    });
    expect(withAuth(undefined, null)).toEqual({});
  });
  it("overrides any caller Authorization with the stored token", () => {
    expect(withAuth({ Authorization: "Bearer old" }, "new").authorization).toBe("Bearer new");
  });
});
