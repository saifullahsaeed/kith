/** What a file is, decided from its name — which language, and whether it is text at all. */


/* ── Extension → language table ─────────────────────────────────────────── */

export type Lang = { id: string; label: string };
const BY_EXT: Record<string, Lang> = {
  js: { id: "javascript", label: "JavaScript" },
  mjs: { id: "javascript", label: "JavaScript" },
  cjs: { id: "javascript", label: "JavaScript" },
  jsx: { id: "javascript", label: "JSX" },
  ts: { id: "typescript", label: "TypeScript" },
  tsx: { id: "typescript", label: "TSX" },
  py: { id: "python", label: "Python" },
  pyw: { id: "python", label: "Python" },
  rb: { id: "ruby", label: "Ruby" },
  go: { id: "go", label: "Go" },
  rs: { id: "rust", label: "Rust" },
  java: { id: "java", label: "Java" },
  kt: { id: "kotlin", label: "Kotlin" },
  swift: { id: "swift", label: "Swift" },
  c: { id: "c", label: "C" },
  h: { id: "c", label: "C" },
  cpp: { id: "cpp", label: "C++" },
  cc: { id: "cpp", label: "C++" },
  cxx: { id: "cpp", label: "C++" },
  hpp: { id: "cpp", label: "C++" },
  cs: { id: "csharp", label: "C#" },
  php: { id: "php", label: "PHP" },
  sh: { id: "bash", label: "Shell" },
  bash: { id: "bash", label: "Shell" },
  zsh: { id: "bash", label: "Shell" },
  json: { id: "json", label: "JSON" },
  jsonc: { id: "json", label: "JSON" },
  yml: { id: "yaml", label: "YAML" },
  yaml: { id: "yaml", label: "YAML" },
  toml: { id: "ini", label: "TOML" },
  ini: { id: "ini", label: "INI" },
  cfg: { id: "ini", label: "Config" },
  conf: { id: "ini", label: "Config" },
  env: { id: "bash", label: "Env" },
  xml: { id: "xml", label: "XML" },
  html: { id: "xml", label: "HTML" },
  htm: { id: "xml", label: "HTML" },
  svg: { id: "xml", label: "SVG" },
  css: { id: "css", label: "CSS" },
  scss: { id: "scss", label: "SCSS" },
  less: { id: "less", label: "Less" },
  sql: { id: "sql", label: "SQL" },
  graphql: { id: "graphql", label: "GraphQL" },
  gql: { id: "graphql", label: "GraphQL" },
  diff: { id: "diff", label: "Diff" },
  patch: { id: "diff", label: "Diff" },
  dockerfile: { id: "dockerfile", label: "Dockerfile" },
  makefile: { id: "makefile", label: "Makefile" },
  lua: { id: "lua", label: "Lua" },
  r: { id: "r", label: "R" },
  pl: { id: "perl", label: "Perl" },
  scala: { id: "scala", label: "Scala" },
};
const BY_NAME: Record<string, Lang> = {
  dockerfile: BY_EXT.dockerfile,
  makefile: BY_EXT.makefile,
  ".gitignore": { id: "bash", label: "gitignore" },
  ".dockerignore": { id: "bash", label: "dockerignore" },
  ".env": BY_EXT.env,
  "requirements.txt": { id: "bash", label: "requirements" },
};
const MARKDOWN = new Set(["md", "markdown", "mdx"]);

/**
 * Pictures the browser draws itself.
 *
 * Every one of these renders in an `<img>` with no library and no decoding on our side,
 * which is the whole argument for showing them here: the viewer was answering "this needs
 * its own application" for a PNG he had just taken a screenshot into.
 *
 * SVG is on the list, and inside an `<img>` rather than inlined into the document. An SVG
 * can carry `<script>`, and inlining one written by an agent would be handing it script
 * execution on this origin; an `<img>` renders the picture with scripts inert. Its source
 * is still one click away, because for an SVG the source is often the interesting part.
 */
const IMAGES: Record<string, string> = {
  png: "PNG",
  jpg: "JPEG",
  jpeg: "JPEG",
  gif: "GIF",
  webp: "WebP",
  avif: "AVIF",
  bmp: "Bitmap",
  ico: "Icon",
  svg: "SVG",
};

/**
 * Things the browser plays.
 *
 * The same argument as `IMAGES`, one medium along, and the gap that produced the worst answer the
 * viewer gave: an `.mp4` matched nothing here, fell through to `plain`, and was read as *text* —
 * so a four-megabyte video came back as "that file is too big to open here (3975240 bytes; max
 * 2000000)". Two wrong statements in one line. It was never going to be text, and the limit it
 * named is the limit on reading text.
 *
 * Every format here is one Chromium decodes natively, which is the whole test for being on the
 * list. Anything else still gets `NeedsAnApp`, which is the honest answer.
 */
const VIDEO: Record<string, string> = {
  mp4: "MP4",
  m4v: "MP4",
  webm: "WebM",
  ogv: "Ogg",
  // Usually H.264 in a QuickTime container, which Chromium plays. When it is ProRes it will not,
  // and the element says so itself rather than this list pretending to know which.
  mov: "QuickTime",
};

const AUDIO: Record<string, string> = {
  mp3: "MP3",
  m4a: "M4A",
  aac: "AAC",
  wav: "WAV",
  flac: "FLAC",
  oga: "Ogg",
  opus: "Opus",
};

/** Mermaid source, which is a picture written down — same argument as SVG, one step earlier. */
const DIAGRAMS = new Set(["mmd", "mermaid"]);
/** A page he wrote to be looked at, not read. Same call as `.mmd`: the thing it draws is the
 *  default and the markup is the toggle. It is also what he actually produces when asked for
 *  something animated — a file, opened — so this is the surface that catches that. */
const PAGES = new Set(["html", "htm"]);

export type Kind =
  | { type: "markdown"; label: string }
  | { type: "diagram"; label: string }
  | { type: "page"; label: string }
  | { type: "code"; lang: string; label: string }
  | { type: "plain"; label: string }
  | { type: "image"; label: string }
  | { type: "video"; label: string }
  | { type: "audio"; label: string }
  | { type: "pdf"; label: string };

export function classify(name: string): Kind {
  const base = (name.split("/").pop() ?? name).toLowerCase();
  const ext = base.includes(".") ? base.split(".").pop()! : "";
  // Before the language tables, because `svg` is in both: it is a picture first and its
  // markup second, and the source toggle is how you get to the markup.
  if (IMAGES[ext]) return { type: "image", label: IMAGES[ext] };
  if (VIDEO[ext]) return { type: "video", label: VIDEO[ext] };
  if (AUDIO[ext]) return { type: "audio", label: AUDIO[ext] };
  if (DIAGRAMS.has(ext)) return { type: "diagram", label: "Diagram" };
  if (PAGES.has(ext)) return { type: "page", label: "Page" };
  if (ext === "pdf") return { type: "pdf", label: "PDF" };
  if (MARKDOWN.has(ext)) return { type: "markdown", label: "Markdown" };
  const named = BY_NAME[base];
  if (named) return { type: "code", lang: named.id, label: named.label };
  const byExt = BY_EXT[ext];
  if (byExt) return { type: "code", lang: byExt.id, label: byExt.label };
  return { type: "plain", label: ext ? ext.toUpperCase() : "Text" };
}

/**
 * Should the caller skip reading this file as text?
 *
 * Exported because the three places that mount the viewer fetch the text body themselves,
 * and for a PNG that fetch is worse than wasted: it reads a megabyte off disk to fail a
 * UTF-8 decode, and that failure is what used to put "This one needs its own application"
 * in front of a screenshot.
 *
 * SVG is the exception and deliberately still fetched. It *is* text — cheap to read, and
 * the source is what you want half the time you open one — so it renders as a picture and
 * keeps the source toggle. Asking the question this way round, rather than "is it media",
 * is what makes that case expressible.
 */
export function skipTextRead(name: string): boolean {
  const kind = classify(name);
  if (kind.type === "video" || kind.type === "audio") return true;
  return kind.type === "pdf" || (kind.type === "image" && kind.label !== "SVG");
}

/** The server says a file isn't text when it can't be decoded — which for a workbook
 *  is the normal case, not a failure. */
export function looksBinary(error: string): boolean {
  return /binary file/i.test(error);
}

/** The server refuses a file past the size it will read as text. That is not an error to print
 *  in red and stop at — the file is fine, it is just not going to be read here, and the way out
 *  is the same two buttons that answer "not text". */
export function looksTooBig(error: string): boolean {
  return /too big to (open|show)/i.test(error);
}
