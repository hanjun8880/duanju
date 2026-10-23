import { describe, expect, it } from "vitest";
import { allReady, buildSpec, isWorking, validateSpec } from "./remix";

describe("remix spec mirrors backend RemixSpec validation", () => {
  it("needs at least one mode", () => {
    expect(validateSpec(buildSpec({ modes: [] }))).toMatch(/至少/);
  });
  it("each mode needs its field", () => {
    expect(validateSpec(buildSpec({ modes: ["restyle"] }))).toMatch(/风格/);
    expect(validateSpec(buildSpec({ modes: ["replot"], plot_direction: "  " }))).toMatch(/剧情/);
    expect(validateSpec(buildSpec({ modes: ["spinoff"] }))).toMatch(/番外/);
    expect(validateSpec(buildSpec({ modes: ["condense"] }))).toMatch(/比例/);
    expect(validateSpec(buildSpec({ modes: ["condense"], condense_ratio: 1 }))).toMatch(/比例/);
  });
  it("sequel and condense cannot combine", () => {
    expect(validateSpec(buildSpec({ modes: ["sequel", "condense"], condense_ratio: 0.5 }))).toMatch(/续写/);
  });
  it("builds a clean payload: recast lines -> map, empty fields dropped", () => {
    const spec = buildSpec({ modes: ["recast", "restyle"], recastText: "林晚=改成男性律师\n\n 周叙 = 去掉 ", style: "民国", plot_direction: "" });
    expect(spec).toEqual({ modes: ["recast", "restyle"], recast: { 林晚: "改成男性律师", 周叙: "去掉" }, style: "民国" });
    expect(validateSpec(spec)).toBeNull();
  });
});

describe("source progress", () => {
  const s = (status: string) => ({ episode_number: 1, status, progress: 0, sha256: null, cache_hit: false, shots: null, kept: null, error: null, video_path: "" });
  it("working while anything is queued/deconstructing/describing", () => {
    expect(isWorking([s("deconstructed"), s("queued")])).toBe(true);
    expect(isWorking([s("deconstructed"), s("failed")])).toBe(false);
  });
  it("ready only when every episode is deconstructed or described", () => {
    expect(allReady([s("deconstructed"), s("described")])).toBe(true);
    expect(allReady([s("deconstructed"), s("registered")])).toBe(false);
    expect(allReady([])).toBe(false);
  });
});
