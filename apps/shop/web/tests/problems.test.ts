import { describe, expect, it } from "vitest";

import { ApiProblem, asProblem, problemOf, unreachableProblem } from "../src/api/problem";
import { fittedSize, photoRejection } from "../src/photo/prepare";

describe("api problems", () => {
  it("explain a malformed boolean query in the shop's words", () => {
    const problem = problemOf(400, { error: "falta un operando", kind: "BooleanQueryError" });
    expect([problem.kind, problem.title, problem.retryable]).toEqual(["rejected", "La búsqueda tiene un operador suelto", false]);
  });

  it("explain photo rejections by their kind", () => {
    expect(problemOf(413, { error: "x", kind: "TooManyPixelsError" }).title).toBe("La foto es demasiado grande");
    expect(problemOf(400, { error: "x", kind: "UnreadablePhotoError" }).detail).toBe("Sube un JPEG, PNG o WebP.");
  });

  it("explain a 413 from nginx, which has no json", () => {
    expect(problemOf(413, null).title).toBe("La foto pesa demasiado");
  });

  it("treat a gateway error or a dead engine as unavailable and retryable", () => {
    for (const problem of [problemOf(502, null), problemOf(503, { error: "x", kind: "EngineUnavailableError" })]) {
      expect([problem.kind, problem.retryable]).toEqual(["unavailable", true]);
    }
  });

  it("ask to wait after too many requests", () => {
    expect(problemOf(429, null).title).toBe("Demasiadas peticiones seguidas");
  });

  it("use the api's own message for an unknown rejection", () => {
    expect(problemOf(400, { error: "el límite es 100", kind: "BadRequestError" }).detail).toBe("El límite es 100.");
  });

  it("say not found for a missing product", () => {
    expect(problemOf(404, { error: "no hay un producto X", kind: "UnknownProductError" }).kind).toBe("not-found");
  });

  it("turn a fetch that never arrived into «the shop does not answer»", () => {
    expect(asProblem(new TypeError("Failed to fetch"))).toEqual(unreachableProblem());
    const known = problemOf(404, null);
    expect(asProblem(known)).toBe(known);
    expect(known).toBeInstanceOf(ApiProblem);
  });
});

describe("photos before uploading", () => {
  it("reject what is not an image or weighs too much", () => {
    expect(photoRejection({ type: "application/pdf", size: 10 })).toBe("not-an-image");
    expect(photoRejection({ type: "image/jpeg", size: 30 * 1024 * 1024 })).toBe("too-heavy");
    expect(photoRejection({ type: "image/heic", size: 2 * 1024 * 1024 })).toBeNull();
  });

  it("shrink to the longest side without ever enlarging", () => {
    expect(fittedSize(4032, 3024)).toEqual({ width: 1024, height: 768 });
    expect(fittedSize(3024, 4032)).toEqual({ width: 768, height: 1024 });
    expect(fittedSize(640, 480)).toEqual({ width: 640, height: 480 });
  });
});
