/** The id the domain field and its list share. One constant, so they cannot drift apart. */
const DOMAIN_LIST = "kith-cookie-domains";

import { useEffect, useState, type FormEvent } from "react";

import { Loader2 } from "lucide-react";

/**
 * The two rows of chrome that fold out of a browser pane's header.
 *
 * ## Why rows and not popovers
 *
 * Every instinct says these should be dropdowns. They cannot be, and the reason is the same one
 * `browser-pane.tsx` opens with: the page is a `WebContentsView` the main process composites
 * **over** the window, so anything Kith draws above the pane appears *underneath* the browser.
 * The pane has a defence against that — `covered()` samples five points of the hole and hides
 * the view when something is on top — but a small popover hanging off a header button covers one
 * corner and no more, so it fails the sample, the view stays, and the menu is behind the page.
 *
 * A row in the header has none of that trouble. It is outside the hole, so opening one makes the
 * hole shorter, the resize observer fires, and the shell moves the page down to match. The
 * mechanism that already keeps the page in its rectangle is the mechanism that gets this right.
 */

const FIELD =
  "bg-muted focus:bg-background focus:ring-ring/40 min-w-0 rounded-md px-2 py-1 text-xs outline-none focus:ring-1";
const CHIP =
  "border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground flex-none rounded-full border px-2 py-0.5 text-[11px] transition-colors";
const CHIP_ON = "border-kith/50 bg-kith-soft text-kith";

type Ask = (verb: string, extra?: Record<string, unknown>) => Promise<Record<string, unknown> | undefined>;

/** The named sizes, and the fact the row exists to make visible: **a viewport is a layout, not a
 *  zoom**. The page is laid out at the size named here whatever the pane can hold, and the scale
 *  beside it says how much of that is being painted. */
const PRESETS: { id: string; label: string }[] = [
  { id: "phone", label: "Phone" },
  { id: "phone-landscape", label: "Phone ↔" },
  { id: "tablet", label: "Tablet" },
  { id: "tablet-landscape", label: "Tablet ↔" },
  { id: "laptop", label: "Laptop" },
  { id: "desktop", label: "Desktop" },
  { id: "square", label: "Square" },
];

const RATIOS = ["16:9", "16:10", "4:3", "3:2", "1:1", "9:16", "21:9"];

export function ViewportRow({
  ask,
  viewport,
  scale,
}: {
  ask: Ask;
  viewport: { width: number; height: number } | null;
  scale: number;
}) {
  const [width, setWidth] = useState("");
  const [height, setHeight] = useState("");
  const [failure, setFailure] = useState("");

  /* Seeded from what is actually in force, and re-seeded whenever that changes — including when
   * the model changes it. Two drivers of one browser is the premise of the whole plugin, so a
   * box showing the size *this person* last typed while the page is laid out at another one is
   * the pane telling a small lie about a thing it is on screen specifically to report. */
  useEffect(() => {
    setWidth(viewport ? String(viewport.width) : "");
    setHeight(viewport ? String(viewport.height) : "");
  }, [viewport?.width, viewport?.height]);

  const send = async (extra: Record<string, unknown>) => {
    setFailure("");
    const answer = await ask("resize", extra);
    if (answer && typeof answer.error === "string") setFailure(answer.error);
  };

  const apply = (event: FormEvent) => {
    event.preventDefault();
    const w = Number(width);
    const h = Number(height);
    // Each dimension travels only if it is a number. They are independent at the other end, so
    // clearing one box and typing in the other means "change this one and leave that one", which
    // is what somebody emptying a field is asking for.
    void send({
      ...(Number.isFinite(w) && w > 0 ? { width: Math.round(w) } : {}),
      ...(Number.isFinite(h) && h > 0 ? { height: Math.round(h) } : {}),
    });
  };

  return (
    <div className="border-border/60 flex flex-none flex-wrap items-center gap-1 border-b px-2 py-1.5">
      <button
        type="button"
        className={`${CHIP} ${viewport ? "" : CHIP_ON}`}
        onClick={() => void send({ preset: "reset" })}
      >
        Full pane
      </button>
      {PRESETS.map((preset) => (
        <button
          key={preset.id}
          type="button"
          className={CHIP}
          onClick={() => void send({ preset: preset.id })}
        >
          {preset.label}
        </button>
      ))}

      <span className="bg-border/60 mx-1 h-4 w-px flex-none" />

      <form className="flex flex-none items-center gap-1" onSubmit={apply}>
        <input
          value={width}
          onChange={(event) => setWidth(event.target.value)}
          onFocus={(event) => event.currentTarget.select()}
          inputMode="numeric"
          placeholder="W"
          aria-label="Viewport width"
          className={`${FIELD} w-14 text-center`}
        />
        <span className="text-muted-foreground/60 text-[11px]">×</span>
        <input
          value={height}
          onChange={(event) => setHeight(event.target.value)}
          onFocus={(event) => event.currentTarget.select()}
          inputMode="numeric"
          placeholder="H"
          aria-label="Viewport height"
          className={`${FIELD} w-14 text-center`}
        />
        <button type="submit" className={CHIP}>
          Set
        </button>
      </form>

      <span className="bg-border/60 mx-1 h-4 w-px flex-none" />

      {/* A ratio settles whichever dimension was not given, so these reshape around the width
          that is already in force rather than replacing it with a size nobody chose. */}
      {RATIOS.map((ratio) => (
        <button key={ratio} type="button" className={CHIP} onClick={() => void send({ ratio })}>
          {ratio}
        </button>
      ))}

      {/* The one number that used not to exist, and whose absence was the bug: a viewport larger
          than the pane is drawn smaller than it is laid out, and saying so is the difference
          between a page you can trust and a page that looks like it did not take. */}
      {viewport && scale < 0.999 ? (
        <span className="text-muted-foreground/70 ml-auto flex-none text-[11px]">
          laid out at {viewport.width}×{viewport.height}, shown at {Math.round(scale * 100)}%
        </span>
      ) : null}
      {failure ? <span className="text-muted-foreground ml-auto text-[11px]">{failure}</span> : null}
    </div>
  );
}

interface Profile {
  id: string;
  name: string;
}

interface Source {
  id: string;
  name: string;
  profiles: Profile[];
}

interface FoundDomain {
  domain: string;
  cookies: number;
  profile: string;
}

/**
 * Bring one site's login over from a browser you are already signed in on.
 *
 * The narrowness is the feature and the row is written to make it obvious: a browser, one
 * domain, and a count of what was copied. `desktop/src/web/cookie-import.ts` carries the long
 * form of why it is shaped this way — that the plugin's profile starting empty is what makes
 * every other act in this pane unprompted, and that a whole-profile copy would be a different
 * bargain nobody asked for.
 */
export function CookieRow({ ask, url }: { ask: Ask; url: string }) {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [from, setFrom] = useState("");
  /** Which profile, when the browser has more than one. "" means all of them, which is what
   *  this did before there was a way to say otherwise. */
  const [profile, setProfile] = useState("");
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  /** What that browser is actually holding a login for. Null until asked. */
  const [known, setKnown] = useState<FoundDomain[] | null>(null);

  useEffect(() => {
    let alive = true;
    void ask("cookie-sources").then((answer) => {
      if (!alive) return;
      const found = Array.isArray(answer?.sources) ? (answer.sources as Source[]) : [];
      setSources(found);
      setFrom((now) => now || found[0]?.id || "");
    });
    return () => {
      alive = false;
    };
  }, [ask]);

  /* What that browser is signed into, asked when you pick one.
   *
   * The domain used to be typed blind: you wrote it, pressed Import, and found out whether you
   * were signed in by whether anything arrived. The database was already opened to answer that —
   * it simply was not asked until after you had guessed. Read-only and no keychain prompt, since
   * only cookie *values* are encrypted and host names never were. */
  useEffect(() => {
    if (!from) return;
    let alive = true;
    setKnown(null);
    void ask("cookie-domains", { source: from, profile: profile || undefined }).then((answer) => {
      if (!alive) return;
      setKnown(Array.isArray(answer?.domains) ? (answer.domains as FoundDomain[]) : []);
    });
    return () => {
      alive = false;
    };
  }, [ask, from, profile]);

  // A profile chosen on one browser means nothing on the next.
  useEffect(() => {
    setProfile("");
  }, [from]);

  /* Seeded with the site in the address bar, because the page you are looking at is the page you
   * hit a login on — that is the moment this row gets opened. */
  useEffect(() => {
    setDomain((now) => {
      if (now) return now;
      try {
        return url ? new URL(url).hostname : "";
      } catch {
        return "";
      }
    });
  }, [url]);

  const chosen = sources?.find((one) => one.id === from);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!from || !domain.trim() || busy) return;
    setBusy(true);
    setSaid("");
    try {
      const answer = await ask("cookie-import", {
        source: from,
        domain: domain.trim(),
        profile: profile || undefined,
      });
      const failure = String(answer?.failure ?? answer?.error ?? "");
      if (failure) {
        setSaid(failure);
        return;
      }
      const added = Number(answer?.added ?? 0);
      const skipped = Number(answer?.skipped ?? 0);
      setSaid(
        added
          ? `${added} cookie${added === 1 ? "" : "s"} copied${skipped ? `, ${skipped} skipped` : ""}. Reload the page.`
          : "Nothing was copied.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="border-border/60 flex flex-none flex-wrap items-center gap-1.5 border-b px-2 py-1.5"
      onSubmit={submit}
    >
      <span className="text-muted-foreground/70 flex-none text-[11px]">Copy the login for</span>
      {/* A text field with a list behind it, rather than one or the other.
          A plain field made you guess; a plain dropdown would refuse a domain the browser holds
          under a name you did not expect. `list` gives the browser's own filtering — typing
          narrows it — and anything typed still goes through `hostnameOf` on the way in. */}
      <input
        value={domain}
        onChange={(event) => setDomain(event.target.value)}
        onFocus={(event) => event.currentTarget.select()}
        spellCheck={false}
        placeholder="example.com"
        aria-label="Domain"
        list={DOMAIN_LIST}
        className={`${FIELD} w-44`}
      />
      <datalist id={DOMAIN_LIST}>
        {(known ?? []).map((one) => (
          <option key={`${one.profile}:${one.domain}`} value={one.domain}>
            {one.cookies} cookie{one.cookies === 1 ? "" : "s"}
            {profile ? "" : ` · ${one.profile}`}
          </option>
        ))}
      </datalist>
      <span className="text-muted-foreground/70 flex-none text-[11px]">from</span>
      <select
        value={from}
        onChange={(event) => setFrom(event.target.value)}
        aria-label="Browser to copy from"
        className={`${FIELD} max-w-48`}
      >
        {sources === null ? <option value="">Looking…</option> : null}
        {sources?.length === 0 ? <option value="">No browsers found</option> : null}
        {sources?.map((source) => (
          <option key={source.id} value={source.id}>
            {source.name}
          </option>
        ))}
      </select>
      {/* Named, not counted. "Google Chrome (4 profiles)" told you how many there were and not
          which one you are signed in on — the only part that matters when one of them is work
          and one is personal. Shown only when there is a choice to make. */}
      {chosen && chosen.profiles.length > 1 ? (
        <select
          value={profile}
          onChange={(event) => setProfile(event.target.value)}
          aria-label="Profile to copy from"
          className={`${FIELD} max-w-40`}
        >
          <option value="">Any profile</option>
          {chosen.profiles.map((one) => (
            <option key={one.id} value={one.id}>
              {one.name}
            </option>
          ))}
        </select>
      ) : null}
      <button type="submit" className={CHIP} disabled={busy || !from || !domain.trim()}>
        {busy ? <Loader2 className="size-3 animate-spin" /> : "Import"}
      </button>
      {/* macOS puts up its own dialog for the browser's key, and somebody who was not expecting
          it reads it as Kith having gone wrong. Saying so first is cheaper than explaining it
          afterwards. */}
      <span className="text-muted-foreground/60 flex-1 text-right text-[11px]">
        {said ||
          (known === null
            ? "macOS will ask for that browser's key. Only this domain is copied."
            : known.length
              ? `${known.length} site${known.length === 1 ? "" : "s"} signed in here. Only this domain is copied.`
              : "No logins found in that browser. Only this domain is copied.")}
      </span>
    </form>
  );
}
