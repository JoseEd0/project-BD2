/**
 * Los códigos de las personas registradas desde este dispositivo. El código es la única
 * llave para ver y borrar un registro y el servicio no lo vuelve a mostrar, así que se
 * guarda aquí; si el almacenamiento no está disponible (navegación privada), la app sigue y
 * el código solo se ve una vez.
 */
export interface StoredPerson {
  readonly personId: string;
  readonly displayName: string;
  readonly expiresAt: string;
}

export const PEOPLE_STORAGE_KEY = "rostros.personas";
export const PERSON_ID_PATTERN = /^[0-9a-f]{32}$/;

function isStoredPerson(value: unknown): value is StoredPerson {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.personId === "string" &&
    PERSON_ID_PATTERN.test(record.personId) &&
    typeof record.displayName === "string" &&
    typeof record.expiresAt === "string"
  );
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function loadPeople(): StoredPerson[] {
  try {
    const parsed: unknown = JSON.parse(storage()?.getItem(PEOPLE_STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter(isStoredPerson) : [];
  } catch {
    return [];
  }
}

function save(people: readonly StoredPerson[]): boolean {
  try {
    const target = storage();
    if (target === null) {
      return false;
    }
    target.setItem(PEOPLE_STORAGE_KEY, JSON.stringify(people));
    return true;
  } catch {
    return false;
  }
}

/** Guarda (o reemplaza) a una persona; `false` si no se pudo guardar. */
export function rememberPerson(person: StoredPerson): boolean {
  return save([...loadPeople().filter((stored) => stored.personId !== person.personId), person]);
}

export function forgetStoredPerson(personId: string): boolean {
  return save(loadPeople().filter((stored) => stored.personId !== personId));
}

/** El código en grupos de cuatro, para leerlo y dictarlo sin perderse. */
export function groupCode(personId: string): string {
  return personId.match(/.{1,4}/g)?.join(" ") ?? personId;
}

/** El código pegado por la persona, sin espacios ni guiones; `null` si no es uno válido. */
export function normalizeCode(input: string): string | null {
  const compact = input.replace(/[\s-]/g, "").toLowerCase();
  return PERSON_ID_PATTERN.test(compact) ? compact : null;
}
