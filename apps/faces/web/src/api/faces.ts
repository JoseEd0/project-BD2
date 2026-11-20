import { createServiceClient } from "@minigestor/web-kit";

import { API_BASE_URL } from "../config";
import type { components, paths } from "./generated/faces";
import { networkProblem, problemFromResponse, type ApiProblem } from "./problems";

export type Consent = components["schemas"]["Consent"];
export type Enrollment = components["schemas"]["Enrollment"];
export type ShotResult = components["schemas"]["ShotResult"];
export type Person = components["schemas"]["Person"];
export type Enrolled = components["schemas"]["Enrolled"];
export type Pose = components["schemas"]["Pose"];
export type Lookalikes = components["schemas"]["Lookalikes"];
export type FamousLookalike = components["schemas"]["FamousLookalike"];

export type Outcome<T> = { readonly ok: true; readonly data: T } | { readonly ok: false; readonly problem: ApiProblem };

/** El código de una persona viaja en una cabecera, nunca en la ruta (no queda en los logs). */
const PERSON_ID_HEADER = "X-Person-Id";
const PHOTO_FIELD = "image";
const PHOTO_NAME = "toma.jpg";

// `fetch` se busca en cada petición y no al crear el cliente: así lo sustituyen los tests.
const client = createServiceClient<paths>({ baseUrl: API_BASE_URL, fetch: (request) => globalThis.fetch(request) });

interface FetchResult<T> {
  readonly data?: T;
  readonly error?: unknown;
  readonly response: Response;
}

async function settle<T>(request: Promise<FetchResult<T>>): Promise<Outcome<T>> {
  try {
    const { data, error, response } = await request;
    if (response.ok) {
      return { ok: true, data: data as T };
    }
    return { ok: false, problem: problemFromResponse(response.status, error) };
  } catch {
    return { ok: false, problem: networkProblem() };
  }
}

function photoForm(photo: Blob): FormData {
  const form = new FormData();
  form.append(PHOTO_FIELD, photo, PHOTO_NAME);
  return form;
}

export function fetchConsent(): Promise<Outcome<Consent>> {
  return settle(client.GET("/consent"));
}

export function startEnrollment(displayName: string, consentVersion: string): Promise<Outcome<Enrollment>> {
  return settle(
    client.POST("/enrollments", {
      body: { display_name: displayName, consent: { version: consentVersion, accepted: true } },
    }),
  );
}

export function submitShot(enrollmentId: string, photo: Blob): Promise<Outcome<ShotResult>> {
  return settle(
    client.POST("/enrollments/{enrollment_id}/shots", {
      params: { path: { enrollment_id: enrollmentId } },
      body: { image: "" },
      bodySerializer: () => photoForm(photo),
    }),
  );
}

export function finishEnrollment(enrollmentId: string): Promise<Outcome<Enrolled>> {
  return settle(client.POST("/enrollments/{enrollment_id}/finish", { params: { path: { enrollment_id: enrollmentId } } }));
}

/** Cancela un registro a medias; borra lo que se haya tomado. Sale aunque se cierre la página. */
export function cancelEnrollment(enrollmentId: string): Promise<Outcome<unknown>> {
  return settle(
    client.DELETE("/enrollments/{enrollment_id}", { params: { path: { enrollment_id: enrollmentId } }, keepalive: true }),
  );
}

export function fetchPerson(personId: string): Promise<Outcome<Person>> {
  return settle(client.GET("/person", { params: { header: { [PERSON_ID_HEADER]: personId } } }));
}

export function forgetPerson(personId: string): Promise<Outcome<unknown>> {
  return settle(client.DELETE("/person", { params: { header: { [PERSON_ID_HEADER]: personId } } }));
}

/** Las personas públicas de LFW más parecidas a la cara de la foto; la foto no se guarda. */
export function findLookalikes(photo: Blob): Promise<Outcome<Lookalikes>> {
  return settle(
    client.POST("/lookalikes", {
      body: { image: "" },
      bodySerializer: () => photoForm(photo),
    }),
  );
}

/** La dirección de la foto de LFW que devuelve `findLookalikes` (`photo_path`). */
export function lookalikePhotoUrl(person: FamousLookalike): string {
  return `${API_BASE_URL}${person.photo_path}`;
}
