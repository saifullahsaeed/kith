/**
 * What the host installs on `window` before a plugin's own code runs.
 *
 * Hand-written rather than imported from a package, because a plugin is a folder someone drops
 * in — there is nothing to install. The shape is the six-message protocol in
 * `ui/src/lib/plugin-bridge.ts`, and the thing worth noticing is what is *absent*: no `fetch`,
 * no `navigate`, no `open`. A surface has no verb that reaches outside its own store. Data
 * arrives because the manifest declared it and the host pushes it; actions happen because the
 * manifest declared a command and the host drew the button.
 */
export type KithValue = string | number | boolean | null | KithValue[] | { [k: string]: KithValue };

declare global {
  interface Window {
    kith: {
      /** This plugin's id, and which of its surfaces this frame is. */
      plugin: string;
      view: string;
      /** Called with the whole store, now and on every change. */
      render(handler: (state: Record<string, KithValue>, host: Record<string, unknown>) => void): void;
      /** Answer a command the host routes in. Unused here — every command is `collect`. */
      on(name: string, handler: (args: Record<string, KithValue>) => unknown): void;
      state: {
        get(key: string): KithValue;
        all(): Record<string, KithValue>;
        /** Merge these keys into the store. The host re-validates and caps everything. */
        set(values: Record<string, KithValue>, expect?: Record<string, number>): void;
        drop(keys: string[]): void;
      };
      /** Ask to be this tall. Ignored for a pane surface, which the layout sizes. */
      size(px: number): void;
    };
  }
}
