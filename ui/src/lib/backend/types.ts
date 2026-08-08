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
  | {
      type: "compacting";
      used?: number;
      window?: number;
      foldedFrom?: number;
      foldedTo?: number;
    }
  | { type: "conversation"; id: string }
  | { type: "error"; message: string }
  | { type: "done" };
