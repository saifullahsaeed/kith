/**
 * Bringing a login you already have into Kith's browser tab.
 *
 * ## The rule this bends, and the one it does not
 *
 * `web-views.ts` makes an argument that the rest of the browser rests on: the pane's session is
 * the browser's own, it starts logged out, and so *anything logged into it is something the
 * person logged in themselves, in this view, on purpose*. That is why no permission prompt
 * appears anywhere on the navigation path — the profile holds no identity nobody chose to put
 * there. It goes on to say, in as many words, that a view onto their real Chrome profile would
 * be a completely different proposition.
 *
 * It would be, and this is not that. What this does is narrower in three ways, and each one is
 * load-bearing rather than a convenience:
 *
 * 1. **One domain at a time, named by the person.** Not a profile, not "everything you are
 *    signed into". The whole point of the ask is the one internal tool behind a login that
 *    takes four minutes to get through by hand; a whole-profile copy would put every live
 *    session on the machine into a browser the model drives, which is a different bargain
 *    entirely and nobody asked for it.
 * 2. **Started by a click on Kith's own chrome, never by a call.** It is on the fixed IPC
 *    channel beside back and reload — it is not an act of the `browser` tool and there is no case
 *    for it in `/browse`, so the model cannot reach it. `web-view-channel.ts` gives the general
 *    form of that argument; this is the entry where it matters most.
 * 3. **The operating system asks too.** Reading a Chromium profile's cookies needs its key out
 *    of the login keychain, and macOS puts up its own dialog for that, naming Kith. A second
 *    consent, from something Kith does not control, on the one action here that deserves it.
 *
 * ## Why shelling out rather than a library
 *
 * The desktop app has no runtime dependencies at all — see `package.json` — and this would
 * otherwise add two: a SQLite binding (native, rebuilt per Electron ABI, a standing tax on
 * every upgrade) and nothing for the keychain that `security` does not already do. macOS ships
 * `/usr/bin/sqlite3` and `/usr/bin/security`. Neither touches the person's profile — the cookie
 * database is copied to a temp folder first — both take their arguments through `execFile`
 * rather than a shell, and the one value interpolated into SQL is checked against a hostname
 * pattern first.
 */

import { execFile } from "node:child_process";
import { createHash, pbkdf2Sync, createDecipheriv } from "node:crypto";
import { copyFileSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

import { session } from "electron";

import { BROWSER_PARTITION } from "../config";

const run = promisify(execFile);

/** Chromium's key derivation on macOS, fixed since the scheme was introduced. Not tunable and
 *  not a preference — these are the numbers Chrome itself uses, and any other set decrypts to
 *  nothing. */
const CHROMIUM_SALT = "saltysalt";
const CHROMIUM_ROUNDS = 1003;
const CHROMIUM_KEY_BYTES = 16;
/** The IV is sixteen spaces. Yes, really. */
const CHROMIUM_IV = Buffer.alloc(16, " ");

/** A profile Kith knows how to read, and where its two secrets live. */
interface Source {
  id: string;
  /** What the person calls it. */
  name: string;
  kind: "chromium" | "firefox";
  /** The profile directory, under the home folder. */
  folder: string;
  /** Keychain service and account, for a Chromium profile. */
  keychain?: { service: string; account: string };
}

/**
 * Every browser this could read from, whether or not it is installed.
 *
 * Arc and the Chromium family all keep `Default/Cookies` under a `User Data` folder and all put
 * their key in the login keychain under "<Name> Safe Storage". Firefox keeps `cookies.sqlite`
 * in a profile folder and encrypts nothing, which makes it the easy one.
 *
 * Safari is deliberately absent. Its cookies are a proprietary `.binarycookies` format needing
 * a parser of its own, and reading them at all needs Full Disk Access — a grant that covers the
 * whole machine, asked for by an app that wanted one domain's cookies. The trade is bad enough
 * that it should be asked for explicitly rather than assumed.
 */
const CATALOGUE: Source[] = [
  {
    id: "chrome",
    name: "Google Chrome",
    kind: "chromium",
    folder: "Library/Application Support/Google/Chrome",
    keychain: { service: "Chrome Safe Storage", account: "Chrome" },
  },
  {
    id: "chrome-beta",
    name: "Chrome Beta",
    kind: "chromium",
    folder: "Library/Application Support/Google/Chrome Beta",
    keychain: { service: "Chrome Safe Storage", account: "Chrome" },
  },
  {
    id: "arc",
    name: "Arc",
    kind: "chromium",
    folder: "Library/Application Support/Arc/User Data",
    keychain: { service: "Arc Safe Storage", account: "Arc" },
  },
  {
    id: "brave",
    name: "Brave",
    kind: "chromium",
    folder: "Library/Application Support/BraveSoftware/Brave-Browser",
    keychain: { service: "Brave Safe Storage", account: "Brave" },
  },
  {
    id: "edge",
    name: "Microsoft Edge",
    kind: "chromium",
    folder: "Library/Application Support/Microsoft Edge",
    keychain: { service: "Microsoft Edge Safe Storage", account: "Microsoft Edge" },
  },
  {
    id: "vivaldi",
    name: "Vivaldi",
    kind: "chromium",
    folder: "Library/Application Support/Vivaldi",
    keychain: { service: "Vivaldi Safe Storage", account: "Vivaldi" },
  },
  {
    id: "chromium",
    name: "Chromium",
    kind: "chromium",
    folder: "Library/Application Support/Chromium",
    keychain: { service: "Chromium Safe Storage", account: "Chromium" },
  },
  {
    id: "firefox",
    name: "Firefox",
    kind: "firefox",
    folder: "Library/Application Support/Firefox/Profiles",
  },
];

export interface FoundProfile {
  /** The profile's folder name — `Default`, `Profile 3`, or Firefox's `abc123.default`. */
  id: string;
  /** What the person named it, when they named it. */
  name: string;
}

export interface FoundSource {
  id: string;
  name: string;
  /** The profiles found in it, named. "Google Chrome (4 profiles)" told you the count and not
   *  which one you are signed in on, which is the only part that matters when you have a work
   *  profile and a personal one. */
  profiles: FoundProfile[];
  /** macOS would not let Kith read this browser's folder. Said, because otherwise it looks like a
   *  browser with one profile and nothing signed in — which is exactly how it looked. */
  blocked: boolean;
}

/** Which of them are actually on this machine. Asked at the moment the person opens the panel
 *  rather than cached, because installing a browser between two openings of a panel is an
 *  ordinary thing to do and a stale list is a browser that cannot be chosen. */
export function sources(): FoundSource[] {
  const found: FoundSource[] = [];
  for (const source of CATALOGUE) {
    const { profiles, blocked } = scan(source);
    if (profiles.length || blocked) {
      found.push({
        id: source.id,
        name: source.name,
        profiles: profiles.map((one) => ({ id: one.id, name: one.name })),
        blocked,
      });
    }
  }
  return found;
}

/** What to tell somebody whose browser's folder macOS will not open. */
export function blockedSaying(name: string): string {
  return (
    `macOS is not letting Kith read ${name}'s data, so its profiles and logins cannot be listed or ` +
    "copied. Allow Kith under System Settings → Privacy & Security → Full Disk Access, then open this again."
  );
}

/** Did this fail because the operating system refused, rather than because nothing was there? */
function refused(error: unknown): boolean {
  const code = (error as NodeJS.ErrnoException | null)?.code;
  return code === "EPERM" || code === "EACCES";
}

/**
 * What a browser calls its own profiles.
 *
 * Chromium keeps them in `Local State`, under `profile.info_cache`, keyed by the same directory
 * name the cookies live in. Firefox keeps them in `profiles.ini`. Neither is a secret and
 * neither needs the keychain — this is the file that says "Saif (Work)" where the folder says
 * "Profile 3".
 *
 * Falls back to the directory name, which is what was shown before: a profile with no name is
 * still a profile you can pick, and a missing `Local State` must not hide a browser.
 */
function namesFor(source: Source): Record<string, string> {
  return namesAt(source.kind, join(homedir(), source.folder));
}

/** The same, against a resolved folder — which is what makes it testable without a real
 *  browser installed. */
export function namesAt(kind: Source["kind"], root: string): Record<string, string> {
  const names: Record<string, string> = {};

  if (kind === "firefox") {
    try {
      // `[Profile0]` blocks with `Name=` and `Path=`. Parsed by hand rather than with an INI
      // library, for the same reason this file shells out rather than adding a SQLite binding:
      // the desktop app has no runtime dependencies and this is twelve lines.
      const text = readFileSync(join(root, "..", "profiles.ini"), "utf8");
      let name = "";
      for (const line of text.split(/\r?\n/)) {
        const said = line.trim();
        if (said.startsWith("[")) name = "";
        else if (said.startsWith("Name=")) name = said.slice(5).trim();
        else if (said.startsWith("Path=") && name) {
          names[said.slice(5).trim().replace(/^Profiles\//, "")] = name;
        }
      }
    } catch {
      /* no profiles.ini — the folder names stand */
    }
    return names;
  }

  try {
    const state = JSON.parse(readFileSync(join(root, "Local State"), "utf8")) as {
      profile?: { info_cache?: Record<string, { name?: string }> };
    };
    for (const [folder, info] of Object.entries(state.profile?.info_cache ?? {})) {
      if (typeof info?.name === "string" && info.name.trim()) names[folder] = info.name.trim();
    }
  } catch {
    /* no Local State, or unreadable — the folder names stand */
  }
  return names;
}

/** Every cookie database belonging to one browser, newest-looking profile first. A person with
 *  work and personal profiles is signed in on one of them, and there is no way to know which
 *  from here — so all of them are read and the domain filter decides. */
interface Profile {
  /** The folder, which is what the cookie database is found under. */
  id: string;
  /** What the person named it, or the folder when they did not. */
  name: string;
  file: string;
}

function scan(source: Source): { profiles: Profile[]; blocked: boolean } {
  return profilesAt(source.kind, join(homedir(), source.folder));
}

/**
 * The profiles in one browser's folder, and whether macOS refused to let us look.
 *
 * **The refusal used to be swallowed**, and it looked like something else entirely. macOS
 * protects other apps' data, and when it says no, a single `stat` can still succeed while listing
 * the folder and copying the database both fail — so Chrome appeared with one profile, no names
 * and nothing signed in, and typing a domain found no cookies. The browser was fine; Kith had
 * lost permission to read it, which happens when the app's signature changes under a rebuild.
 */
export function profilesAt(kind: Source["kind"], root: string): { profiles: Profile[]; blocked: boolean } {
  if (!existsSync(root)) return { profiles: [], blocked: false };
  const listing = list(root);
  if (listing === "refused") return { profiles: [], blocked: true };
  const names = namesAt(kind, root);
  const found: Profile[] = [];

  if (kind === "firefox") {
    for (const entry of listing) {
      const file = join(root, entry, "cookies.sqlite");
      if (existsSync(file)) found.push({ id: entry, name: names[entry] || entry, file });
    }
    return { profiles: found, blocked: false };
  }

  // Chromium: `Default`, `Profile 1`, `Profile 2`… and, since the network service moved, the
  // database is a level deeper in `Network/`. Both locations are checked because which one a
  // given browser uses depends on its version rather than on its name.
  for (const entry of ["Default", ...listing.filter((one) => one.startsWith("Profile "))]) {
    for (const where of [join(root, entry, "Network", "Cookies"), join(root, entry, "Cookies")]) {
      if (existsSync(where)) {
        found.push({ id: entry, name: names[entry] || entry, file: where });
        break;
      }
    }
  }
  return { profiles: found, blocked: false };
}

function list(root: string): string[] | "refused" {
  try {
    return readdirSync(root, { withFileTypes: true })
      .filter((one) => one.isDirectory())
      .map((one) => one.name);
  } catch (error) {
    return refused(error) ? "refused" : [];
  }
}

export interface ImportResult {
  added: number;
  /** Cookies that were read but could not be written — an expired one, a malformed host. Worth
   *  reporting rather than hiding: "12 added" when 40 were found is a question. */
  skipped: number;
  /** Which browsers actually had anything for this domain, so "0 added" can say whether the
   *  domain was wrong or the browser was. */
  looked: string[];
  failure: string;
}

/**
 * Copy one domain's cookies out of another browser and into the browser tab's partition.
 *
 * Reads every profile the chosen browser has, because a person with work and personal profiles
 * is signed in on exactly one of them and nothing here can tell which.
 *
 * The domain is checked against a hostname pattern before it goes anywhere near SQL, and the
 * match is written out as three explicit comparisons rather than a suffix `LIKE`. That is not
 * defensiveness about the string — it is a correctness bug avoided: `LIKE '%example.com'` also
 * matches `notexample.com`, so a sloppy filter would quietly import a stranger's cookies along
 * with the ones that were asked for.
 */
export async function importCookies(
  sourceId: string,
  rawDomain: string,
  /** One profile, when the person picked one. Every profile when they did not — which is what
   *  this always did, and stays the right default: a domain is usually signed in on exactly one
   *  of them and reading the rest costs nothing but finds nothing. */
  profileId?: string,
): Promise<ImportResult> {
  const empty: ImportResult = { added: 0, skipped: 0, looked: [], failure: "" };
  const source = CATALOGUE.find((one) => one.id === sourceId);
  if (!source) return { ...empty, failure: `${sourceId} is not a browser I can read.` };

  const domain = hostnameOf(rawDomain);
  if (!domain) {
    return {
      ...empty,
      failure: `"${rawDomain}" is not a domain. Give one like example.com — the cookies for that site and its subdomains are what gets copied.`,
    };
  }
  if (await isPublicSuffix(domain)) {
    return {
      ...empty,
      failure: `${domain} is shared by many unrelated sites, so its cookies would be all of their logins at once. Name the one site — something.${domain}.`,
    };
  }

  const { profiles: all, blocked } = scan(source);
  if (blocked) return { ...empty, failure: blockedSaying(source.name) };
  if (!all.length) return { ...empty, failure: `${source.name} is not installed here.` };
  const chosen = profileId ? all.filter((one) => one.id === profileId) : all;
  if (!chosen.length) {
    return { ...empty, failure: `${source.name} has no profile called ${profileId}.` };
  }
  const files = chosen.map((one) => one.file);

  let key: Buffer | null = null;
  if (source.kind === "chromium") {
    try {
      key = await chromiumKey(source);
    } catch (error) {
      return {
        ...empty,
        failure: `macOS would not give me ${source.name}'s key (${(error as Error).message}). It asks once, in a dialog naming Kith — if you dismissed it, try again and choose Allow.`,
      };
    }
  }

  const jar = session.fromPartition(BROWSER_PARTITION).cookies;
  const result: ImportResult = { added: 0, skipped: 0, looked: [], failure: "" };
  let denied = 0;
  const now = Date.now() / 1000;

  for (const file of files) {
    let rows: Record<string, string | number | null>[];
    try {
      rows = await query(file, source.kind === "firefox" ? firefoxSql(domain) : chromiumSql(domain));
    } catch (error) {
      // One unreadable profile is not the whole import. A browser that is running holds a lock
      // the copy already works around; anything else here is a profile shape this does not know
      // — except the operating system saying no, which is said.
      if (refused(error)) denied += 1;
      continue;
    }
    if (!rows.length) continue;
    result.looked.push(file);

    for (const row of rows) {
      const host = String(row.host ?? "");
      const name = String(row.name ?? "");
      if (!host || !name) {
        result.skipped += 1;
        continue;
      }
      let value = String(row.value ?? "");
      if (source.kind === "chromium" && key) {
        const sealed = String(row.enc ?? "");
        if (sealed) {
          const opened = decryptChromium(sealed, key, host);
          if (opened === null) {
            result.skipped += 1;
            continue;
          }
          value = opened;
        }
      }

      const expires = Number(row.expires ?? 0);
      // A session cookie has no expiry and is passed on as one: `expirationDate` left off is
      // exactly what makes it a session cookie in the destination too.
      if (expires && expires <= now) {
        result.skipped += 1;
        continue;
      }
      try {
        await jar.set(cookieFor(row, name, value, expires));
        result.added += 1;
      } catch {
        result.skipped += 1;
      }
    }
  }

  if (!result.added && !result.skipped) {
    result.failure =
      denied === files.length
        ? blockedSaying(source.name)
        : `${source.name} has no cookies for ${domain}. Check the spelling, and that it is the browser you are signed in on.`;
  }
  return result;
}

/**
 * One source row as the details Electron's `cookies.set` takes.
 *
 * **`domain` only for a cookie that had one.** A leading dot in the source's host is what marks
 * a cookie as "this site and its subdomains"; no dot means host-only. Electron makes the same
 * distinction by whether `domain` is *present*, and this passed it for every row — which turned
 * every host-only cookie into a domain cookie sent to subdomains it was never set for, and made
 * every `__Host-` cookie fail outright, because that prefix is a promise the cookie has no
 * domain. Google and GitHub keep sessions under `__Host-`, so an import could report forty
 * cookies copied and leave you signed out.
 */
export function cookieFor(
  row: Record<string, string | number | null>,
  name: string,
  value: string,
  expires: number,
): Electron.CookiesSetDetails {
  const host = String(row.host ?? "");
  const secure = Number(row.secure ?? 0) === 1;
  const path = String(row.path ?? "/") || "/";
  return {
    url: `${secure ? "https" : "http"}://${host.replace(/^\./, "")}${path}`,
    name,
    value,
    ...(host.startsWith(".") ? { domain: host } : {}),
    path,
    secure,
    httpOnly: Number(row.httponly ?? 0) === 1,
    // `no_restriction` is only accepted on a secure cookie, and a cross-site cookie that
    // arrives as `unspecified` is one the destination will not send where the source did.
    sameSite: sameSiteOf(Number(row.samesite ?? -1), secure),
    ...(expires ? { expirationDate: expires } : {}),
  };
}

/**
 * Is this a suffix many unrelated sites sit under — `co.uk`, `github.io`, `vercel.app`?
 *
 * The domain match takes subdomains, which is right for `github.com` and a disaster for
 * `vercel.app`: every app anybody deployed there is a subdomain of it, and "one domain" would
 * copy the logins for all of them the person has ever visited. `hostnameOf` cannot tell — both
 * are two labels with a dot between — and the list that can is the Public Suffix List.
 *
 * **Asked of the Chromium already in this app rather than shipped as a copy.** The desktop app has
 * no runtime dependencies (see the module comment), and Chromium carries the list, private
 * registries included, because it enforces the same rule on cookies: a cookie may not be set for
 * a public suffix. So a cookie is offered for the domain in a throwaway in-memory session and
 * Chromium's refusal is the answer. Any other refusal is not treated as one — this narrows an
 * import, it must never be the reason a legitimate one cannot happen.
 */
export async function isPublicSuffix(domain: string): Promise<boolean> {
  const jar = session.fromPartition("kith-suffix-check").cookies;
  const url = `https://kith-check.${domain}/`;
  try {
    await jar.set({ url, name: "kith-check", value: "1", domain: `.${domain}` });
    await jar.remove(url, "kith-check");
    return false;
  } catch (error) {
    return /domain/i.test(String((error as Error).message ?? ""));
  }
}

/** What the person typed, as a hostname. Accepts a pasted URL, because that is what is on the
 *  clipboard when somebody is looking at the site they want to bring a login over from. */
export function hostnameOf(raw: string): string {
  let wanted = String(raw ?? "")
    .trim()
    .toLowerCase();
  if (!wanted) return "";
  if (wanted.includes("/") || wanted.includes(":")) {
    try {
      wanted = new URL(wanted.includes("://") ? wanted : `https://${wanted}`).hostname;
    } catch {
      return "";
    }
  }
  wanted = wanted.replace(/^\.+/, "");
  // A hostname, with at least one dot. Nothing that could be a wildcard, a quote or a comment —
  // this is the one value that reaches SQL, and the check is what makes that safe.
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(wanted)) return "";
  if (wanted.length > 253) return "";
  return wanted;
}

/** The domain and its subdomains, written out. See `importCookies` for why this is not a
 *  suffix `LIKE`. */
export function domainMatch(column: string, domain: string): string {
  const quoted = `'${domain}'`;
  const dotted = `'.${domain}'`;
  const under = `'%.${domain}'`;
  return `(${column} = ${quoted} OR ${column} = ${dotted} OR ${column} LIKE ${under})`;
}

function chromiumSql(domain: string): string {
  // `hex(encrypted_value)`, because a BLOB in JSON output comes back mangled and the ciphertext
  // is the whole point. `value` is still read: a cookie written before the profile was encrypted
  // is plaintext there and has an empty blob.
  return `SELECT host_key AS host, name, value, path, expires_utc AS raw_expires,
            is_secure AS secure, is_httponly AS httponly, samesite,
            hex(encrypted_value) AS enc
          FROM cookies WHERE ${domainMatch("host_key", domain)}`;
}

/** Firefox's own jar only. `originAttributes` is empty for it and carries a container's
 *  `userContextId` or a partition key otherwise — and a person with a work container and a
 *  personal one signed into the same site has two sets of the same cookie names, of which the
 *  import kept whichever happened to be written last. */
function firefoxSql(domain: string): string {
  return `SELECT host, name, value, path, expiry AS raw_expires,
            isSecure AS secure, isHttpOnly AS httponly, sameSite AS samesite, '' AS enc
          FROM moz_cookies WHERE originAttributes = '' AND ${domainMatch("host", domain)}`;
}

/**
 * Read a cookie database that another program is holding open.
 *
 * Copied first, and the `-wal` and `-shm` alongside it. A running Chrome keeps its cookie
 * database locked and, more to the point, keeps recent writes in the write-ahead log — so a copy
 * of the main file alone is a database missing whatever was written since the last checkpoint,
 * which in practice is the login that was just completed.
 */
async function query(file: string, sql: string): Promise<Record<string, string | number | null>[]> {
  const scratch = mkdtempSync(join(tmpdir(), "kith-cookies-"));
  try {
    const copy = join(scratch, "db");
    copyFileSync(file, copy);
    for (const suffix of ["-wal", "-shm"]) {
      if (existsSync(file + suffix)) copyFileSync(file + suffix, copy + suffix);
    }
    /* Not `-readonly`, deliberately. What is being opened is a *copy* in a temp folder — the
     * person's profile is never opened at all — and SQLite cannot replay a write-ahead log
     * without being able to write, so a read-only open fails on exactly the profiles that
     * matter: the ones belonging to a browser that is currently running. */
    const { stdout } = await run("/usr/bin/sqlite3", ["-json", copy, sql], {
      maxBuffer: 16 * 1024 * 1024,
      timeout: 20_000,
    });
    const rows = JSON.parse(stdout.trim() || "[]") as Record<string, string | number | null>[];
    return rows.map((row) => ({ ...row, expires: epochOf(row) }));
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

/** Both clocks, as seconds since 1970. Chromium counts microseconds since 1601 — the Windows
 *  epoch, in a file format that outlived the reason — and Firefox counts seconds since 1970. A
 *  cookie imported against the wrong one is either already expired or good for four centuries. */
export function epochOf(row: Record<string, string | number | null>): number {
  const raw = Number(row.raw_expires ?? 0);
  if (!raw) return 0;
  // Anything this large is the 1601 epoch in microseconds; a 1970 epoch in seconds is ~1.8e9.
  if (raw > 1e12) return Math.round(raw / 1e6 - 11_644_473_600);
  return Math.round(raw);
}

export function sameSiteOf(raw: number, secure: boolean): "unspecified" | "no_restriction" | "lax" | "strict" {
  if (raw === 2) return "strict";
  if (raw === 1) return "lax";
  if (raw === 0) return secure ? "no_restriction" : "unspecified";
  return "unspecified";
}

/**
 * The profile's encryption key, out of the login keychain.
 *
 * **This is where macOS asks.** `security` puts up its own dialog naming Kith and the item it
 * wants, and nothing here can suppress it or answer it. That prompt is the second consent this
 * feature rests on — the first being the person having opened the panel and typed a domain — and
 * it is the reason the whole thing can be a button rather than a setting.
 */
async function chromiumKey(source: Source): Promise<Buffer> {
  if (!source.keychain) throw new Error("no keychain entry");
  const { stdout } = await run(
    "/usr/bin/security",
    ["find-generic-password", "-wa", source.keychain.account, "-s", source.keychain.service],
    { timeout: 60_000 },
  );
  const password = stdout.trim();
  if (!password) throw new Error("the keychain returned nothing");
  return pbkdf2Sync(password, CHROMIUM_SALT, CHROMIUM_ROUNDS, CHROMIUM_KEY_BYTES, "sha1");
}

/**
 * One cookie's value, out of its ciphertext.
 *
 * `v10` names the scheme; what follows is AES-128-CBC under the derived key with an IV of
 * sixteen spaces. Null rather than a throw for anything that does not come apart, because one
 * unreadable cookie among forty is a cookie to skip and not an import to abandon.
 *
 * The `host` argument is for the part that is easy to miss: **Chrome 130 and later prepend a
 * SHA-256 of the cookie's own host to the plaintext**, so a decrypt that ignores it returns
 * thirty-two bytes of hash with the session token stuck on the end, and the cookie is written
 * to the destination intact-looking and useless. It is stripped only when it actually matches,
 * so an older profile is unaffected.
 */
function decryptChromium(hex: string, key: Buffer, host: string): string | null {
  try {
    const blob = Buffer.from(hex, "hex");
    const scheme = blob.subarray(0, 3).toString("latin1");
    if (scheme !== "v10" && scheme !== "v11") return null;
    const decipher = createDecipheriv("aes-128-cbc", key, CHROMIUM_IV);
    decipher.setAutoPadding(true);
    let out = Buffer.concat([decipher.update(blob.subarray(3)), decipher.final()]);
    if (out.length > 32) {
      const stamp = createHash("sha256").update(host).digest();
      if (out.subarray(0, 32).equals(stamp)) out = out.subarray(32);
    }
    return out.toString("utf8");
  } catch {
    return null;
  }
}

export interface FoundDomain {
  domain: string;
  /** How many cookies are held for it. A site you are genuinely signed into has several; one
   *  stray analytics cookie has one, and the ordering is what separates them without anybody
   *  having to explain the difference. */
  cookies: number;
  /** Which profile it came from, so "signed in on Work but not Personal" is visible rather than
   *  something you discover by importing and finding nothing. */
  profile: string;
}

/**
 * What this browser is actually holding a login for.
 *
 * Until now the domain was typed blind: you wrote `example.com`, pressed Import, and found out
 * whether you were signed in by whether anything arrived. The database was already being opened
 * to answer that question — it just was not asked until after you had guessed.
 *
 * Read-only and no keychain. Host names are not encrypted in either format; only cookie *values*
 * are, which is what the key is for. So this costs no prompt and reveals nothing a cookie
 * import would not have revealed anyway — it stays behind the same chrome-only entry point as
 * everything else here, unreachable by the model.
 *
 * Grouped and summed across profiles per domain would hide the thing worth knowing, so it is
 * not: one row per domain per profile, and the caller decides how to show that.
 */
export async function domainsIn(
  sourceId: string,
  profileId?: string,
): Promise<{ domains: FoundDomain[]; blocked: boolean }> {
  const source = CATALOGUE.find((one) => one.id === sourceId);
  if (!source) return { domains: [], blocked: false };

  const { profiles: all, blocked } = scan(source);
  if (blocked) return { domains: [], blocked: true };
  const chosen = profileId ? all.filter((one) => one.id === profileId) : all;
  const found: FoundDomain[] = [];
  let denied = 0;

  for (const profile of chosen) {
    let rows: Record<string, string | number | null>[];
    try {
      rows = await query(
        profile.file,
        source.kind === "firefox"
          ? "SELECT host AS h, COUNT(*) AS n FROM moz_cookies WHERE originAttributes = '' GROUP BY host ORDER BY n DESC LIMIT 400"
          : "SELECT host_key AS h, COUNT(*) AS n FROM cookies GROUP BY host_key ORDER BY n DESC LIMIT 400",
      );
    } catch (error) {
      // A locked or missing database is one profile that cannot answer, not a failed listing.
      if (refused(error)) denied += 1;
      continue;
    }

    for (const row of rows) {
      // Both formats store a leading dot for "and its subdomains". It is noise in a list you
      // are choosing from, and `hostnameOf` would strip it on the way back in anyway.
      const domain = String(row.h ?? "").replace(/^\./, "");
      const cookies = Number(row.n ?? 0);
      if (!domain || !cookies) continue;
      found.push({ domain, cookies, profile: profile.name });
    }
  }

  // Most-cookied first, which puts a real login above a tracking pixel, then alphabetical so the
  // tail is somewhere rather than in insertion order.
  return {
    domains: found.sort((a, b) => b.cookies - a.cookies || a.domain.localeCompare(b.domain)),
    blocked: chosen.length > 0 && denied === chosen.length,
  };
}

