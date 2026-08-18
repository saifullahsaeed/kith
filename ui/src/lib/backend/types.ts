/** Chat parameters mirrored from the server's `/api/config`. */
export interface ServerConfig {
  model: string;
  numCtx: number;
  numPredict: number;
  system: string;
  think: boolean;
  /** "", "low", "medium" or "high" — how hard he thinks before answering. */
  effort: string;
  /** What this model accepts. `known` false means nobody has asked the provider yet, which
   *  is not the same as "no" — see model_capabilities on the server. */
  capabilities?: {
    known: boolean;
    images: boolean;
    files: boolean;
    reasoning: boolean;
    modalities: string[];
  };
  baseUrl: string; // OpenAI-compatible cloud endpoint; blank = local Ollama
  apiKeySet: boolean; // whether a cloud key is stored (the key itself is never returned)
}

export type JsonValue =
  string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

/** One category's share of the context window. */
export interface ContextLine {
  key: string;
  label: string;
  tokens: number;
  share: number;
}

/**
 * What is in the model's context right now, by category.
 *
 * `window` is 0 when nobody can say how big it is — a local model, or one adopted before its
 * size was recorded — and the meter must render nothing rather than a made-up percentage.
 * The token counts are still real in that case; only the shares are unavailable.
 */
export interface ContextLedger {
  window: number;
  used: number;
  free: number;
  share: number;
  lines: ContextLine[];
  /** Characters per token, as the provider's own billing calibrated it for the round this was
   *  taken on. Nothing displays it — `/fold` needs it to convert the characters it removed into
   *  this reading's own units. Absent on anything recorded before the field existed. */
  charsPerToken?: number;
}

/** One event in the server's newline-delimited chat stream. */
export type BackendEvent =
  | { type: "delta"; role: "reasoning" | "text"; text: string }
  | { type: "tool_call"; id: string; name: string; arguments: JsonObject }
  | { type: "tool_result"; id: string; name: string; result: JsonValue }
  | { type: "stats"; stats: Record<string, number> }
  | { type: "context"; context: ContextLedger }
  /* Two things emit this, with different things to say about it.
   *
   * Before the turn starts, the prompt build folds the conversation — and on a long one that
   * is a summarisation call large enough to be the whole perceived wait, so it reports how
   * much prose it removed. Mid-turn, the loop folds to make room and reports the window it
   * was up against. Both mean "making room, this will take a moment"; neither field is
   * present in both cases. */
  /* A round failed in a way worth trying again — a dropped connection, a 502, a rate limit —
   * and the loop is waiting before it does. Shown because the wait is otherwise a pause with
   * nothing in it, which is indistinguishable from the hang it is recovering from. */
  | { type: "retrying"; attempt: number; message: string }
  | {
      type: "compacting";
      used?: number;
      window?: number;
      foldedFrom?: number;
      foldedTo?: number;
    }
  /* What you said to a turn already running, at the moment it actually went in.
   *
   * The server has always sent this (`agent_loop` yields it the round it appends the text) and
   * nothing here listened, so the event fell through `adapter`'s final `else { continue }` and
   * was dropped. The result was the worst version of a working feature: the words reached the
   * model, the model acted on them, and the one participant who never saw them was the person
   * who typed them. */
  | { type: "steered"; text: string }
  | { type: "conversation"; id: string }
  | { type: "error"; message: string }
  | { type: "done" };
