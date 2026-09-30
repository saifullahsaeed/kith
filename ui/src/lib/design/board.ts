/**
 * The board: where a task's designs sit next to each other.
 *
 * The editor answers "is this screen right". A board answers the question you actually ask of
 * design work, which is "is this one better than that one" — four structures against three data
 * states, laid out so the comparison is a glance rather than a memory test. That is a different
 * surface, not a bigger version of the same one.
 *
 * ── Why a board is not a thing he creates ───────────────────────────────────────────────────
 *
 * Its frames are the task's HTML deliverables, discovered. There is no board tool, no manifest he
 * has to author, and no order he has to get right: he attaches a design the way he already
 * attaches a file, and it appears on the board. That is the same bet `detect.ts` makes and the
 * same one the ```html canvas won on — a surface that requires wanting a new shape does not get
 * used.
 *
 * It also means every task that already has designs on it has a board today, including work done
 * before any of this existed.
 *
 * ── What is stored, and what is not ─────────────────────────────────────────────────────────
 *
 * Only what cannot be derived: where you dragged something, and what you wrote on it. Everything
 * else — which frames exist, what they contain, their order — comes from the deliverables. So the
 * stored file is small, it is never the source of truth about the design itself, and losing it
 * costs you an arrangement rather than any work.
 */

/** Where one design sits on the board. Keyed by the deliverable's path. */
export interface BoardFrame {
  x: number;
  y: number;
  /** Board-space width. The design renders at its own size and is scaled to fit this. */
  w: number;
}

/** Something you wrote on the board, next to the thing it is about. */
export interface BoardNote {
  id: string;
  x: number;
  y: number;
  w: number;
  text: string;
}

/** The viewport, so reopening a board puts you back where you were looking. */
export interface BoardView {
  x: number;
  y: number;
  zoom: number;
}

export interface Board {
  frames: Record<string, BoardFrame>;
  notes: BoardNote[];
  view?: BoardView;
}

/** A frame's default width in board units, and the gutter between them. */
export const FRAME_W = 440;
const GAP = 64;
/** The box a design is given before its size is known, and the unit its height is capped in. */
export const FRAME_H = 560;

/** A design's authored size — what its `data-k-frame` declares. See `frame.ts`. */
export interface Size {
  width: number;
  height: number;
}

/**
 * How tall a frame is drawn: the design's own proportions at the frame's width.
 *
 * One function because two places need the same answer — the frame that draws the box, and the
 * layout that decides where the next box goes. They used to disagree: frames took their height
 * from the design while the grid still stepped by a fixed `FRAME_H`, so a row of phones (440
 * wide is ~950 tall) covered the bottom third of the row above it.
 *
 * Clamped so one very long page cannot make every other frame unreadably small.
 */
export function frameHeight(frame: Pick<BoardFrame, "w">, size?: Size): number {
  if (!size) return FRAME_H;
  return Math.min(FRAME_H * 3, Math.round(frame.w * (size.height / size.width)));
}

/** How far the board can be zoomed.
 *
 * The ceiling was 2, on the reasoning that past it there was nothing to see. That is true of a
 * board being used to *compare* screens and false of one being used to check a screen: at 2x a
 * 1600px dense table is still 8px type, and "is that column clipping" is exactly the question
 * you open a board to answer. 8 is the point where a hairline border is unmistakable.
 *
 * The floor stays where it is — below it nothing is legible and the board is a map. */
export const ZOOM_RANGE = [0.08, 8] as const;

export const EMPTY: Board = { frames: {}, notes: [] };

/** Where the board for one task is kept. */
export function boardPath(taskId: number): string {
  return `boards/task-${taskId}.board.json`;
}

/**
 * The board as JSON, refused rather than trusted.
 *
 * It is a file in his workspace, so he can write to it and so can anything else that can write a
 * file. Nothing here reaches the DOM, but a NaN coordinate silently removes a frame from the
 * visible world, which is a bug nobody can diagnose by looking. Every number is checked and
 * anything unreadable falls back to the derived layout, which is always correct.
 */
export function readBoard(text: string): Board {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return EMPTY;
  }
  if (!raw || typeof raw !== "object") return EMPTY;
  const doc = raw as Record<string, unknown>;

  const frames: Record<string, BoardFrame> = {};
  const given = doc.frames;
  if (given && typeof given === "object" && !Array.isArray(given)) {
    for (const [file, value] of Object.entries(given as Record<string, unknown>)) {
      const frame = value as Record<string, unknown>;
      if (!frame || typeof frame !== "object") continue;
      const x = Number(frame.x);
      const y = Number(frame.y);
      const w = Number(frame.w);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      frames[file] = { x, y, w: Number.isFinite(w) && w > 80 ? w : FRAME_W };
    }
  }

  const notes: BoardNote[] = [];
  if (Array.isArray(doc.notes)) {
    for (const value of doc.notes) {
      const note = value as Record<string, unknown>;
      if (!note || typeof note !== "object") continue;
      const x = Number(note.x);
      const y = Number(note.y);
      const w = Number(note.w);
      const text = typeof note.text === "string" ? note.text : "";
      const id = typeof note.id === "string" && note.id ? note.id : `n${notes.length + 1}`;
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      notes.push({ id, x, y, w: Number.isFinite(w) && w > 40 ? w : 240, text: text.slice(0, 2000) });
    }
  }

  let view: BoardView | undefined;
  const v = doc.view as Record<string, unknown> | undefined;
  if (v && typeof v === "object") {
    const x = Number(v.x);
    const y = Number(v.y);
    const zoom = Number(v.zoom);
    if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(zoom)) {
      view = { x, y, zoom: Math.min(ZOOM_RANGE[1], Math.max(ZOOM_RANGE[0], zoom)) };
    }
  }

  return { frames, notes, ...(view ? { view } : {}) };
}

export function writeBoard(board: Board): string {
  return `${JSON.stringify(board, null, 2)}\n`;
}

/**
 * Every frame's position: what was stored, and a place for everything that was not.
 *
 * A design attached a minute ago has no stored position, and the answer must not be "it is at
 * 0,0 underneath the first one". New frames are laid out in a grid after the ones that have
 * been placed, in the order the deliverables came, so attaching a design puts it somewhere
 * visible without anyone arranging anything.
 */
export function layout(
  files: string[],
  board: Board,
  sizes: Record<string, Size> = {},
  columns = 3,
): Record<string, BoardFrame> {
  const placed: Record<string, BoardFrame> = {};
  const loose: string[] = [];

  for (const file of files) {
    const stored = board.frames[file];
    if (stored) placed[file] = stored;
    else loose.push(file);
  }

  // Start below whatever has been arranged, so an auto-placed frame never lands on a deliberate
  // one — the arrangement is the part a person made by hand and the part worth protecting.
  const bottom = Object.entries(placed).reduce(
    (low, [file, frame]) => Math.max(low, frame.y + frameHeight(frame, sizes[file])),
    0,
  );
  let top = Object.keys(placed).length ? bottom + GAP : 0;

  // Each row is as tall as its tallest frame, so a row of desktops is not spaced like phones.
  for (let at = 0; at < loose.length; at += columns) {
    const row = loose.slice(at, at + columns);
    row.forEach((file, column) => {
      placed[file] = { x: column * (FRAME_W + GAP), y: top, w: FRAME_W };
    });
    top += Math.max(...row.map((file) => frameHeight(placed[file], sizes[file]))) + GAP;
  }

  return placed;
}

/** The smallest rectangle holding everything, so "fit to screen" has something to fit to. */
export function extent(
  frames: Record<string, BoardFrame>,
  notes: BoardNote[],
  sizes: Record<string, Size> = {},
) {
  const boxes = [
    ...Object.entries(frames).map(([file, f]) => ({ x: f.x, y: f.y, w: f.w, h: frameHeight(f, sizes[file]) })),
    ...notes.map((n) => ({ x: n.x, y: n.y, w: n.w, h: 140 })),
  ];
  if (!boxes.length) return { x: 0, y: 0, w: FRAME_W, h: FRAME_H };

  const left = Math.min(...boxes.map((b) => b.x));
  const top = Math.min(...boxes.map((b) => b.y));
  const right = Math.max(...boxes.map((b) => b.x + b.w));
  const bottom = Math.max(...boxes.map((b) => b.y + b.h));
  return { x: left, y: top, w: right - left, h: bottom - top };
}
