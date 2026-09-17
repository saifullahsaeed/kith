/**
 * The envelope every tool result arrives in, opened in one place.
 *
 * The server wraps each tool's return value before it leaves: `tools/__init__.py` builds
 * `{ok: true, result: <what the tool returned>}`, or `{ok: false, error: ...}`, and nothing
 * between there and the screen peels it — not the stream, not the transcript, not the adapter,
 * not assistant-ui, which carries `result` as opaque.
 *
 * So `part.result` is the envelope, and the payload is one level down. Reading the outer object
 * is a silent, total failure: every field the caller wanted is `undefined`, so a guard rejects
 * it and the component renders nothing at all. It has now happened twice in this codebase — once
 * to a shell run that displayed "no output" while holding plenty of it, and once to the design
 * strip, which returned null for every turn that had designs in it.
 *
 * Lifted out of `tool-result.tsx` the second time, because the first fix's own comment said the
 * point was to unwrap "in one place" and there were by then two.
 */
export interface Unwrapped {
  /** What the tool actually returned. */
  value: unknown;
  /** Whether the envelope says the call failed, which is otherwise invisible. */
  failed: boolean;
}

export function unwrap(result: unknown): Unwrapped {
  if (result && typeof result === "object" && !Array.isArray(result)) {
    const outer = result as Record<string, unknown>;
    if ("ok" in outer && "result" in outer) {
      return { value: outer.result, failed: outer.ok === false };
    }
    if ("ok" in outer && "error" in outer) {
      return { value: outer.error, failed: outer.ok === false };
    }
  }
  // Not everything is enveloped — a plugin tool's result arrives as itself, and treating that as
  // a missing payload would break the renderers that work today.
  return { value: result, failed: false };
}
