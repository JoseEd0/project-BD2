import type { Enrollment, Person, ShotResult } from "../src/api/faces";

export const ENROLLMENT_ID = "e".repeat(32);
export const PERSON_ID = "0123456789abcdef0123456789abcdef";

export function enrollment(covered: Enrollment["covered"] = [], message = "Mira directo a la cámara."): Enrollment {
  const poses: Enrollment["poses"] = ["front", "left", "right"];
  return {
    enrollment_id: ENROLLMENT_ID,
    display_name: "Ana",
    poses,
    covered,
    remaining: poses.filter((pose) => !covered.includes(pose)),
    attempts_left: 20 - covered.length,
    expires_at: "2026-11-19T12:15:00Z",
    message,
  };
}

export function accepted(covered: Enrollment["covered"]): ShotResult {
  const pose = covered[covered.length - 1] ?? "front";
  return { accepted: true, reason: null, message: "Toma aceptada.", pose, quality: null, enrollment: enrollment(covered) };
}

export function rejected(reason: ShotResult["reason"], message: string, covered: Enrollment["covered"] = []): ShotResult {
  return { accepted: false, reason, message, pose: null, quality: null, enrollment: enrollment(covered) };
}

export const PERSON: Person = {
  person_id: PERSON_ID,
  display_name: "Ana",
  consent_version: "1-abcdef123456",
  consented_at: "2026-11-19T12:00:00Z",
  created_at: "2026-11-19T12:01:00Z",
  expires_at: "2026-12-19T12:01:00Z",
  shots: 3,
};
