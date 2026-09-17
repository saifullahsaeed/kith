/**
 * The pictures in a design, fetched rather than linked.
 *
 * ── Why a relative src does not simply work ─────────────────────────────────────────────────
 *
 * `<img src="assets/logo.svg">` survives the whitelist — a relative workspace path is exactly
 * what `safeUrl` is for — and then resolves against the app's own origin, where that path is not
 * a file but a route. It does not even 404: the SPA answers every unmatched path with
 * `index.html`, so the browser is handed a document where it expected a picture and draws a
 * broken box. Silently, and with a 200.
 *
 * ── Why not just point it at the raw route ──────────────────────────────────────────────────
 *
 * The obvious fix is `src="/api/workspace/raw?path=…"`, and it works in a browser tab and fails
 * in the product. The API token is added by a patch over `window.fetch` (`lib/api-token.ts`); an
 * `<img>` does not go through `fetch`, so in the packaged app every asset comes back 401 —
 * a class of bug that is invisible in development and total in the build.
 *
 * So each asset is fetched, which carries the token, and the bytes become an object URL. That
 * also means an asset that is missing is *known* to be missing, rather than being a broken box
 * nobody can explain.
 */
import { useEffect, useState } from "react";

import { rawFileUrl } from "@/lib/files";

/** What a design asked for, and what it actually got. */
export type Assets = Record<string, string>;

/** Paths that need fetching: workspace-relative, not already a data URI. */
export function assetPaths(values: Iterable<string>): string[] {
  const out = new Set<string>();
  for (const value of values) {
    const path = value.trim();
    if (!path || path.startsWith("data:") || path.startsWith("blob:")) continue;
    if (/^[a-z][a-z0-9+.-]*:/i.test(path)) continue;
    out.add(path);
  }
  return [...out];
}

/**
 * Object URLs for each path, revoked when they stop being needed.
 *
 * Keyed by the path so two designs showing the same logo fetch it once per view — enough
 * de-duplication for a board of a dozen frames, and no cache to invalidate when he replaces the
 * file.
 */
export function useAssets(paths: string[], projectId: number | null): Assets {
  const [assets, setAssets] = useState<Assets>({});
  // A stable key, so an array rebuilt each render does not refetch every picture on every
  // keystroke in the inspector.
  const key = paths.join("");

  useEffect(() => {
    if (!key) {
      setAssets({});
      return;
    }
    let alive = true;
    const made: string[] = [];

    void Promise.all(
      key.split("").map(async (path) => {
        try {
          const response = await fetch(rawFileUrl(path, projectId));
          if (!response.ok) return [path, ""] as const;
          const url = URL.createObjectURL(await response.blob());
          made.push(url);
          return [path, url] as const;
        } catch {
          return [path, ""] as const;
        }
      }),
    ).then((pairs) => {
      if (!alive) {
        for (const url of made) URL.revokeObjectURL(url);
        return;
      }
      setAssets(Object.fromEntries(pairs.filter(([, url]) => url)));
    });

    return () => {
      alive = false;
      for (const url of made) URL.revokeObjectURL(url);
    };
  }, [key, projectId]);

  return assets;
}
