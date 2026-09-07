/**
 * What a plugin surface may say, and the thing that refuses to believe most of it.
 *
 * `canvas-bridge.ts` is the sibling of this file and the reasoning there applies here in full:
 * everything arriving is written by a third party, running in a frame that was sealed precisely
 * because it is not trusted, so a message is *evidence of an event* — never an instruction and
 * never a value to pass along unexamined.
 *
 * Three rules, carried over verbatim because they were right:
 *
 * * **Identity before content.** The only trustworthy fact about a message from an opaque frame
 *   is which window sent it — `event.origin` is the string `"null"` for every sandboxed frame on
 *   the page, so it distinguishes nothing. The check is `event.source === frame.contentWindow`,
 *   and it lives in the component because that is the only place holding the ref.
 * * **A closed vocabulary.** Four message types, all listed below. Anything else is dropped
 *   silently rather than logged, because a page that can make the app write to a console it
 *   shares with the person has already gained something.
 * * **Shapes, not values.** Every field is bounded. A surface cannot send prose, so it cannot
 *   smuggle a paragraph of instructions into a turn dressed as a stored value.
 *
 * ## What is different from a canvas, and why
 *
 * A canvas reading rides on one message and is then gone, which is why `canvas-state` is right
 * to forget it on unmount. Plugin state survives the frame, lands in a table, and is read into
 * the prompt of every turn for as long as the plugin is installed. Same discipline, separate
 * vocabulary and separate caps, because one name for two lifetimes is exactly the drift
 * `canvasTokens()` exists to prevent.
 *
 * **The frame has no verb that reaches anything outside the plugin's own store.** There is no
 * `fetch`, no `navigate`, no `invoke`, no `open`. That is a real bound and it is what keeps
 * every privileged effect behind a button core drew — which in turn is what makes "a person
 * clicked it, so it is authorised" safe to rely on.
 */

/** The envelope version, shared with the canvas so there is one number to bump. */
export { PROTOCOL } from "@/lib/canvas-bridge";
import { PROTOCOL } from "@/lib/canvas-bridge";

/** How many keys one write may carry. More than this is a surface synchronising its whole model
 *  rather than recording what changed, and the store has a per-slot cap behind this anyway. */
export const MAX_WRITE_KEYS = 16;
/** Serialised bytes per value, re-clamped server-side. A key is a record, not a file. */
export const MAX_VALUE_BYTES = 8_000;
/** Keys, and the same grammar the server enforces. Drift here is a surface that works in one
 *  place and not the other, with no error anywhere — hence the constants test. */
const KEY = /^[a-z0-9][a-z0-9._-]{0,63}$/;
/** A reply field's ceiling, before the command's own declared `maxLength` narrows it. */
export const MAX_REPLY_BYTES = 2_000;
/** Bytes in one file a surface hands back. Just under what `read_image` will accept, so a
 *  surface cannot produce something the tool that reads it would then reject. */
export const MAX_FILE_BYTES = 2_800_000;
/** What a surface may hand back — things a person or the model can look at, and nothing
 *  executable. Kept in step with `services/plugins/state.FILE_KINDS` by hand. */
export const FILE_KINDS = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "image/svg+xml",
  "application/pdf",
  "text/csv",
  "text/plain",
  "application/json",
]);

export type PluginValue = string | number | boolean | null | PluginValue[] | { [k: string]: PluginValue };

export type PluginMessage =
  /** "I am up." Carries the protocol it was built against — see `readPluginMessage`. */
  | { type: "ready"; protocol: number }
  /** "This is what I am now holding." */
  | { type: "state.set"; values: Record<string, PluginValue>; expect?: Record<string, number> }
  /** "I no longer need these." */
  | { type: "state.drop"; keys: string[] }
  /** An answer to a command core asked. */
  | { type: "result"; call: string; ok: boolean; value: Record<string, PluginValue> }
  /** "I would like to be this tall." Ignored for a pane surface, which the layout sizes. */
  | { type: "size"; px: number }
  /** Bytes the surface produced — a rendered image, an export.
   *
   * The one message carrying something unbounded, which is why it is bounded here twice: a
   * declared type from a closed list, and a ceiling the server re-checks. It exists because a
   * surface has no other way to hand bytes to Kith — the store caps a value at 8 KB because it
   * feeds the prompt, and the frame has no network. */
  | { type: "file.put"; id: string; name: string; mime: string; bytes: ArrayBuffer };

/**
 * One message from a frame, or null.
 *
 * **`protocol` is load-bearing on `ready`.** An unrecognised envelope version is dropped, which
 * is what makes a stale cached canvas harmless — and that rule works only while the only
 * consumer is a document Kith generated seconds ago. A third party ships against this, so a
 * mismatch has to be *visible*: the caller mounts a placeholder saying the surface was built for
 * a different version of Kith, rather than a live frame that silently answers nothing.
 */
export function readPluginMessage(data: unknown): PluginMessage | null {
  if (!data || typeof data !== "object") return null;
  const message = data as Record<string, unknown>;
  if (message.kith !== PROTOCOL) return null;

  switch (message.type) {
    case "ready":
      return { type: "ready", protocol: Number(message.protocol) || 0 };

    case "state.set": {
      const values = bounded(message.values);
      if (!values) return null;
      return { type: "state.set", values, expect: revisions(message.expect) };
    }

    case "state.drop": {
      if (!Array.isArray(message.keys)) return null;
      const keys = message.keys
        .filter((key): key is string => typeof key === "string" && KEY.test(key))
        .slice(0, MAX_WRITE_KEYS);
      return keys.length ? { type: "state.drop", keys } : null;
    }

    case "result": {
      if (typeof message.call !== "string" || !message.call) return null;
      const value = bounded(message.value, MAX_REPLY_BYTES) ?? {};
      return { type: "result", call: message.call, ok: message.ok === true, value };
    }

    case "file.put": {
      // An ArrayBuffer, never base64: base64 is a third larger, and this codebase has paid for
      // base64 arriving somewhere that counted it as text once already.
      const bytes = message.bytes;
      if (!(bytes instanceof ArrayBuffer) || bytes.byteLength === 0) return null;
      if (bytes.byteLength > MAX_FILE_BYTES) return null;
      const mime = String(message.mime ?? "").split(";")[0].trim().toLowerCase();
      // A closed list, and nothing executable. A plugin writing a script into a folder Kith can
      // run would be a plugin out of its boundary through the front door.
      if (!FILE_KINDS.has(mime)) return null;
      return {
        type: "file.put",
        id: typeof message.id === "string" ? message.id : "",
        // A hint for a directory listing, not a filename: the server composes the real one, so
        // a name from an untrusted page cannot be a path.
        name: String(message.name ?? "").slice(0, 60),
        mime,
        bytes,
      };
    }

    case "size": {
      const px = Number(message.px);
      // The same range a canvas may claim: a line of content at the floor, a few screens at the
      // ceiling, past which the answer is a wider pane rather than a taller box.
      if (!Number.isFinite(px) || px < 80 || px > 2400) return null;
      return { type: "size", px: Math.round(px) };
    }

    default:
      // Silently. See the module note: a console the app shares with the person is not
      // somewhere an untrusted page gets to write.
      return null;
  }
}

/** A record of bounded values, or null when there is nothing usable in it. */
function bounded(raw: unknown, ceiling = MAX_VALUE_BYTES): Record<string, PluginValue> | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const out: Record<string, PluginValue> = {};
  let count = 0;
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (count >= MAX_WRITE_KEYS) break;
    if (!KEY.test(key)) continue;
    const kept = clamp(value, ceiling);
    if (kept === undefined) continue;
    out[key] = kept;
    count += 1;
  }
  return count ? out : null;
}

/** One value, bounded by its serialised size. Structure is allowed in the *store* — it is the
 *  digest that refuses anything but a primitive, because that is the part reaching a prompt. */
function clamp(value: unknown, ceiling: number): PluginValue | undefined {
  if (value === null) return null;
  const kind = typeof value;
  if (kind === "string") return (value as string).slice(0, ceiling);
  if (kind === "number") return Number.isFinite(value as number) ? (value as number) : undefined;
  if (kind === "boolean") return value as boolean;
  if (kind === "object") {
    let serialised: string;
    try {
      serialised = JSON.stringify(value);
    } catch {
      // A cycle, or something that will not serialise. Dropped rather than repaired.
      return undefined;
    }
    if (!serialised || serialised.length > ceiling) return undefined;
    return JSON.parse(serialised) as PluginValue;
  }
  return undefined;
}

function revisions(raw: unknown): Record<string, number> | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const out: Record<string, number> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const at = Number(value);
    if (KEY.test(key) && Number.isInteger(at) && at >= 0) out[key] = at;
  }
  return Object.keys(out).length ? out : undefined;
}
