import { describe, expect, it, vi } from "vitest";

import { PEOPLE_STORAGE_KEY, forgetStoredPerson, groupCode, loadPeople, normalizeCode, rememberPerson } from "../src/storage/people";
import { PERSON_ID } from "./fixtures";

const ANA = { personId: PERSON_ID, displayName: "Ana", expiresAt: "2026-12-19T12:01:00Z" };

describe("codes kept on this device", () => {
  it("remembers, replaces and forgets a person", () => {
    expect(loadPeople()).toEqual([]);
    expect(rememberPerson(ANA)).toBe(true);
    expect(rememberPerson({ ...ANA, displayName: "Ana María" })).toBe(true);
    expect(loadPeople()).toEqual([{ ...ANA, displayName: "Ana María" }]);
    expect(forgetStoredPerson(PERSON_ID)).toBe(true);
    expect(loadPeople()).toEqual([]);
  });

  it("ignores what someone else left under the key", () => {
    window.localStorage.setItem(PEOPLE_STORAGE_KEY, JSON.stringify([ANA, { personId: "../x" }, 3]));
    expect(loadPeople()).toEqual([ANA]);
    window.localStorage.setItem(PEOPLE_STORAGE_KEY, "{not json");
    expect(loadPeople()).toEqual([]);
  });

  it("says so when the browser does not let it save", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    expect(rememberPerson(ANA)).toBe(false);
    setItem.mockRestore();
  });

  it("groups the code to read it and accepts it pasted with spaces", () => {
    expect(groupCode(PERSON_ID)).toBe("0123 4567 89ab cdef 0123 4567 89ab cdef");
    expect(normalizeCode(" 0123 4567 89AB CDEF-0123 4567 89ab cdef ")).toBe(PERSON_ID);
    expect(normalizeCode("xyz")).toBeNull();
  });
});
