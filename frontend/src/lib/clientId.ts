// An anonymous id for this browser. The backend scopes every conversation to it, so one visitor cannot list or
// open another's chats. It is not a login: whoever holds the id holds the chats, which is why it is a random
// UUID that never leaves this browser except in the X-Client-Id header to our own backend.

const STORAGE_KEY = "nutriai.clientId";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

let current: string | null = null;

function randomUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // crypto.randomUUID exists only in secure contexts (https or localhost); a plain-http dev URL falls back.
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(bytes);
  else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * The id, created and saved on first use. If storage is unavailable (private window, blocked site data) the id
 * lives only as long as the page: chats still work, but a reload starts with an empty list.
 */
export function getClientId(): string {
  if (current) return current;
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && UUID.test(saved)) return (current = saved.toLowerCase());
  } catch {
    // Storage can throw when blocked; fall through to a page-lifetime id.
  }
  current = randomUuid();
  try {
    window.localStorage.setItem(STORAGE_KEY, current);
  } catch {
    // Not persisted; see above.
  }
  return current;
}

/** For tests: forget the cached id so the next call reads storage again. */
export function resetClientIdCache(): void {
  current = null;
}
