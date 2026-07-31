/**
 * Present the API token on every call, from one place.
 *
 * The server now requires a shared secret on every `/api` request — see
 * `server/kith/api/auth.py` for why loopback and CORS were not enough. That means ~50 call
 * sites across this app suddenly need a header, and adding it to each of them would
 * guarantee that the one someone forgets fails at runtime, in a corner, as a 401 that looks
 * like a server fault.
 *
 * So `fetch` is wrapped once, before React mounts. Every existing call site keeps working
 * untouched, and a new one written next month cannot forget.
 *
 * Where the value comes from differs by how the app is being served, and both paths are
 * real:
 *
 * * **Packaged / served by the Kith server** — the same process serves this page and the
 *   API, so it injects `window.__kithToken` into the document it sends. There is nowhere
 *   else it could come from: the token is a 0600 file, and an endpoint that handed it out
 *   would have to be unauthenticated, which is the same hole wearing a hat.
 * * **Vite dev server** — no injection is possible, so the dev proxy reads the token file
 *   from disk and adds the header as it forwards. `window.__kithToken` is simply absent and
 *   this adds nothing, which is exactly right: two copies of the header would be worse
 *   than one.
 *
 * Only `/api` paths are touched. Wrapping every fetch in the app and attaching a secret to
 * whatever URL happened to be passed is how a local token ends up in someone else's server
 * log.
 */
declare global {
  interface Window {
    __kithToken?: string;
  }
}

const HEADER = "X-Kith-Token";

/** Is this one of our own API calls, rather than a request to the wider world? */
function ours(input: RequestInfo | URL): boolean {
  const raw =
    typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
  if (raw.startsWith("/api/")) return true;
  // Absolute URLs too, but only when they point back at this origin — a same-path URL on
  // another host is not ours no matter how much it looks like it.
  try {
    const url = new URL(raw, window.location.href);
    return url.origin === window.location.origin && url.pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

export function installApiToken(): void {
  const token = window.__kithToken;
  if (!token) return; // dev: the proxy adds it

  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    if (!ours(input)) return original(input, init);
    // Headers rather than a plain object: callers pass all three accepted shapes (object,
    // array of pairs, Headers) and spreading a Headers instance silently yields nothing —
    // which would drop the caller's own Content-Type and break every POST.
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    headers.set(HEADER, token);
    return original(input, { ...init, headers });
  };
}
