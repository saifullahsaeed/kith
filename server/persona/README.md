# Persona

Kith's system prompt is assembled from the files in this folder. Each `.md` or
`.txt` file is one instruction ("fragment"); they are merged, in order, into a
single system prompt **at request time** — so edits here take effect on the next
message, no restart needed.

## Rules

- **Order** — fragments merge in path order, with numbers compared as numbers, so
  `9-` really does come before `10-`. Use numeric prefixes (`00-`, `10-`, `20-`, …)
  to control the sequence. Subfolders are allowed for grouping.
- **Disable** — prefix a file or folder with `_` or `.` to skip it
  (e.g. `10-tone.md` → `_10-tone.md`).
- **Comments** — `<!-- ... -->` HTML comments are stripped, so you can annotate a
  fragment without it leaking into the prompt. A leading comment is shown as
  "Your note" in Settings → Persona, in its own field above the text.
- **README** files (like this one) are ignored.

## The fragments

| File | What it holds |
|------|----------------|
| `00-who.md` | Who he is, in three sentences |
| `05-where-you-are.md` | What he may touch, and where his mess goes |
| `10-how-you-judge.md` | Checking before claiming, not folding, not inventing decisions |
| `15-how-you-work.md` | Proving a change ran, and keeping a way back |
| `17-asked-to-think.md` | Telling a question from an instruction |
| `20-how-you-answer.md` | Reply shape |
| `25-what-you-draw.md` | When to draw instead of describe |
| `30-how-you-read.md` | Reading code by structure |
| `35-how-you-spend-a-round.md` | Batching independent tool calls |

## Adding an instruction

Drop a new file in, named to sit where you want it in the order:

```sh
echo "Prefer short answers unless asked to elaborate." > persona/50-brevity.md
```

## Override

- `KITH_PERSONA_DIR=/path/to/dir` — load fragments from a different folder.
- `KITH_SYSTEM="..."` — bypass this folder entirely with one inline prompt.
