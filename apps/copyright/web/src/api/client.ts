import { createServiceClient } from "@minigestor/web-kit";

import { API_BASE_URL } from "../config";
import type { paths } from "./generated/copyright";

export const copyrightClient = createServiceClient<paths>({ baseUrl: API_BASE_URL });

/** El audio original de una canción, servido por trozos (`Range`) para que el reproductor salte. */
export function songAudioUrl(songId: number): string {
  return `${API_BASE_URL}/songs/${encodeURIComponent(String(songId))}/audio`;
}
