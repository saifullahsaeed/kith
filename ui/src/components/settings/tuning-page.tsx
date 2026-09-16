import { useMemo, useState } from "react";

import {
  boundError,
  defaultNote,
  NumberField,
  Segmented,
  SettingRow,
  SettingRows,
  SettingSection,
  Switch,
} from "@/components/settings/setting-row";
import { SaveBar } from "@/components/settings/save-bar";
import { saveTuning, type Tunable, type TunableGroup } from "@/lib/backend";

/**
 * Any slice of the server's tunable groups, as a page.
 *
 * This is what replaced Advanced. That page held every group the server sends, which is why it
 * had grown a tab strip of its own — thirty-one settings under seven headings is not a page,
 * it is four pages that had not been separated yet. The groups were already the right division;
 * they were a division you could not land on.
 *
 * So the component is parameterised by *which* groups rather than duplicated per page: Budget
 * and pace, Context, Folders and files and MCP are four instances of this, and the mapping from
 * server group to page lives in one table in `settings-page.tsx`. A group the server adds that
 * nobody has placed still appears — see `TUNING_PAGES` — rather than silently vanishing, which
 * is what a hand-written page per group would do.
 */
export function TuningPage({
  groups,
  onSaved,
  bare,
  rename,
}: {
  /** Already filtered to this page's groups, in the order they should read. */
  groups: TunableGroup[];
  onSaved: () => void;
  /** Embedded in a page that already supplies the scroller and the measure. */
  bare?: boolean;
  /** Override a group's heading. The server's labels are written to stand alone; on a page that
   *  already says "MCP servers" at the top, "Other programs' tools" is the same words twice. */
  rename?: Record<string, string>;
}) {
  /* Edits, keyed by setting. Held as the raw string a field contains rather than as a parsed
   * number: a half-typed "1" on the way to "120" is not the number 1, and parsing on every
   * keystroke is what makes a field you cannot clear to retype. */
  const [draft, setDraft] = useState<Record<string, string | boolean>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const all = useMemo(() => groups.flatMap((group) => group.settings), [groups]);

  const edited = useMemo(
    () => all.filter((knob) => knob.key in draft && String(draft[knob.key]) !== String(knob.value)),
    [all, draft],
  );
  const refused = useMemo(
    () =>
      edited.filter(
        (knob) => typeof draft[knob.key] === "string" && boundError(knob, draft[knob.key] as string),
      ),
    [edited, draft],
  );

  async function save() {
    setSaving(true);
    setError("");
    try {
      const updates: Record<string, number | string | boolean> = {};
      for (const knob of edited) {
        const held = draft[knob.key];
        updates[knob.key] =
          knob.kind === "bool"
            ? Boolean(held)
            : knob.kind === "text"
              ? String(held)
              : Number(held);
      }
      await saveTuning(updates);
      // The server clamps, so what comes back is what he will actually use — drop the draft and
      // let the refetched snapshot be the truth rather than keeping a value it may have moved.
      setDraft({});
      onSaved();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      {/* A measure on the page, not only on the help line.
       *
       * These settings carry real explanations — several run to five or six lines, because the
       * thing they control has a consequence worth a paragraph. On a full-width pane that puts
       * the control four hundred pixels to the right of the sentence explaining it, which is
       * two columns pretending to be a row. Capped, the control stays beside what it belongs
       * to at any window size. */}
      <div className={bare ? "" : "min-h-0 flex-1 overflow-y-auto px-8 pt-1 pb-7"}>
        {/* `SettingSection` zeroes its top margin when it is first in its parent. Embedded, it
            is first here but second on the page, so the margin has to come back. */}
        <div className={bare ? "mt-6" : ""}>
        {groups.map((group) => (
          <SettingSection key={group.key} title={rename?.[group.key] ?? group.label} sub={group.blurb}>
            <SettingRows>
              {group.settings.map((knob) => (
                <Knob
                  key={knob.key}
                  knob={knob}
                  draft={draft[knob.key]}
                  onChange={(value) => setDraft((was) => ({ ...was, [knob.key]: value }))}
                />
              ))}
            </SettingRows>
          </SettingSection>
        ))}
        </div>
      </div>

      <SaveBar
        changed={edited.map((knob) => knob.label.toLowerCase())}
        saving={saving}
        error={error || (refused.length ? "Some values are out of range." : "")}
        onRevert={() => {
          setDraft({});
          setError("");
        }}
        onSave={() => void save()}
        sticky={bare}
      />
    </>
  );
}

/** One `Tunable`, as whichever control it is. */
function Knob({
  knob,
  draft,
  onChange,
}: {
  knob: Tunable;
  draft: string | boolean | undefined;
  onChange: (value: string | boolean) => void;
}) {
  const held = draft ?? knob.value;
  const edited = draft !== undefined && String(draft) !== String(knob.value);
  const error = typeof held === "string" ? boundError(knob, held) : "";

  return (
    <SettingRow
      label={knob.label}
      help={knob.help + defaultNote(knob)}
      // Away from its default, or away from what is saved. Both are "this is not the value it
      // would have without you", which is what the dot is for.
      changed={!knob.isDefault || edited}
      error={error}
      disabled={knob.fromEnv}
      disabledWhy={`Set by ${knob.env} at launch, so the field here would do nothing.`}
    >
      {knob.kind === "bool" ? (
        <Switch
          checked={Boolean(held)}
          label={knob.label}
          disabled={knob.fromEnv}
          onChange={onChange}
        />
      ) : knob.choices?.length ? (
        <Segmented
          options={knob.choices.map((choice) => ({ value: choice, label: choice }))}
          value={String(held)}
          disabled={knob.fromEnv}
          onChange={onChange}
        />
      ) : (
        <NumberField
          value={String(held)}
          unit={knob.unit}
          invalid={Boolean(error)}
          disabled={knob.fromEnv}
          wide={knob.kind === "text"}
          onChange={onChange}
        />
      )}
    </SettingRow>
  );
}
