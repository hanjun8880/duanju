import { describe, expect, it } from "vitest";
import openapi from "./openapi.json";
import { JobCreateRequest, MultimodalRefs } from "./contracts";

const schemas = (openapi as { components: { schemas: Record<string, { properties: Record<string, unknown>; required?: string[] }> } }).components.schemas;

describe("Zod contracts mirror the FastAPI/Pydantic OpenAPI schema", () => {
  it.each([
    ["JobCreateRequest", JobCreateRequest],
    ["MultimodalRefs", MultimodalRefs],
  ] as const)("%s has exactly the backend fields", (name, zod) => {
    expect(Object.keys(zod.shape).sort()).toEqual(Object.keys(schemas[name].properties).sort());
    const required = Object.entries(zod.shape).filter(([, v]) => !v.isOptional()).map(([k]) => k).sort();
    expect(required).toEqual([...(schemas[name].required ?? [])].sort());
  });

  it("enforces the Seedance reference limits client-side", () => {
    expect(MultimodalRefs.safeParse({ image_urls: Array(31).fill("x") }).success).toBe(false);
    expect(MultimodalRefs.safeParse({ image_urls: Array(30).fill("x") }).success).toBe(true);
  });
});
