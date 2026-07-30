/** Kith's felt state — a word, an energy level, and why. */

export interface Mood {
  label: string;
  energy: number;
  note: string;
  updated_at: string | null;
}

export async function fetchMood(): Promise<Mood> {
  const res = await fetch("/api/mood");
  if (!res.ok) throw new Error(`/api/mood ${res.status}`);
  return (await res.json()) as Mood;
}

// Freeform mood labels → a hue for the orb and the ambient wash. Robust to
// whatever word he picks; falls back to his amber.
const HUES: [RegExp, string][] = [
  [/content|calm|settl|good|warm|happy|peace|glad|serene|ease/i, "var(--kith)"],
  [/restless|stuck|frustrat|tense|anx|agitat|impatient|unsettl/i, "oklch(0.72 0.15 42)"],
  [/wist|lonely|quiet|tired|low|melanchol|blue|weary|subdued|somber/i, "oklch(0.7 0.085 250)"],
  [/curious|excit|alive|eager|bright|inspired|playful|energ|keen/i, "oklch(0.82 0.15 132)"],
];

export function moodHue(label: string | undefined): string {
  if (label) for (const [re, hue] of HUES) if (re.test(label)) return hue;
  return "var(--kith)";
}
