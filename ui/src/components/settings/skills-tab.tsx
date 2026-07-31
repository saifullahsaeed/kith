import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, FolderOpen, Loader2, Puzzle, ShieldAlert, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { openOnHost } from "@/lib/files";
import {
  fetchSkills,
  installSkill,
  pickFolder,
  removeSkill,
  type SkillsSnapshot,
} from "@/lib/backend";
import { cn } from "@/lib/utils";

/**
 * Skills — capabilities you install into him, in a format he did not invent.
 *
 * A skill is a folder with a SKILL.md in it: what it is, when to use it, then instructions
 * and optionally scripts and reference files. It is the Agent Skills open standard, so a
 * skill written for Claude Code, Cursor or Gemini CLI works here unmodified.
 *
 * Two things this screen exists to make true rather than to claim.
 *
 * **The cost is shown, in the units that matter.** Every skill puts its name and description
 * in his prompt on every request, and nothing else — the instructions are only read when he
 * decides one applies. That is the difference between installing ten skills and installing
 * ten copies of a manual, and it is invisible unless someone says the number out loud. So
 * the header does: what the whole set costs, against what it would cost inlined.
 *
 * **Installing is the person's decision, deliberately.** There is no "browse and install"
 * button and no URL field, because a skill is executable instructions from elsewhere and the
 * standard's own documentation is blunt about what a malicious one can do. Installing means
 * pointing at a folder already on this machine — one you could have read first.
 */
export function SkillsTab() {
  const confirm = useConfirm();
  const [snapshot, setSnapshot] = useState<SkillsSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchSkills()
      .then(setSnapshot)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(load, [load]);

  async function add() {
    setError("");
    const picked = await pickFolder("Choose a skill folder (the one containing SKILL.md)");
    if (picked === null) {
      setError("No desktop app running, so there's no folder chooser.");
      return;
    }
    if (!picked) return;
    setBusy(true);
    try {
      await installSkill(picked);
      load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function drop(name: string) {
    const ok = await confirm({
      title: "Remove this skill?",
      subject: name,
      description:
        "He stops being able to use it. The folder goes to your Trash, so nothing you wrote " +
        "in it is destroyed.",
      confirmLabel: "Remove",
      destructive: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await removeSkill(name);
      load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!snapshot) {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-sm">
        <Loader2 className="size-4 animate-spin" /> Reading his skills…
      </p>
    );
  }

  const { skills, problems } = snapshot;
  const inlined = skills.reduce((sum, skill) => sum + skill.bodyChars, 0);

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <p className="text-muted-foreground min-w-0 flex-1 text-xs leading-relaxed">
          A skill is a folder of instructions for one kind of work — the{" "}
          <span className="text-foreground">Agent Skills</span> standard, so anything written for
          Claude Code, Cursor or Gemini CLI works here as-is. He sees only the name and what each is
          for; he reads the instructions when a task actually calls for one.
        </p>
        <Button size="sm" variant="outline" onClick={() => void add()} disabled={busy}>
          <FolderOpen className="size-3.5" />
          Install from a folder
        </Button>
      </div>

      {/* The number that decides whether installing another one is free. */}
      <div className="border-border/60 bg-card/40 flex flex-wrap items-baseline gap-x-6 gap-y-1 rounded-xl border p-3">
        <span className="text-sm font-medium">
          {skills.length === 0
            ? "Nothing installed"
            : `${skills.length} skill${skills.length === 1 ? "" : "s"}`}
        </span>
        {skills.length > 0 ? (
          <>
            <span className="text-muted-foreground text-xs">
              <span className="text-foreground font-mono tabular-nums">
                {snapshot.indexTokens.toLocaleString()}
              </span>{" "}
              tokens in every prompt, inside the cached part
            </span>
            <span className="text-muted-foreground/60 text-xs">
              vs{" "}
              <span className="font-mono tabular-nums">
                {Math.round(inlined / 3.7).toLocaleString()}
              </span>{" "}
              if their instructions were loaded too
            </span>
          </>
        ) : null}
      </div>

      {problems.length > 0 ? (
        <div className="border-destructive/40 bg-destructive/5 space-y-1.5 rounded-xl border p-3">
          <p className="flex items-center gap-2 text-xs font-medium">
            <AlertTriangle className="text-destructive size-3.5" />
            {problems.length} folder{problems.length === 1 ? "" : "s"} couldn&rsquo;t be read
          </p>
          {problems.map((problem) => (
            <p key={problem.name} className="text-muted-foreground pl-6 text-[11px]">
              <span className="font-mono">{problem.name}</span> — {problem.error}
            </p>
          ))}
        </div>
      ) : null}

      {skills.length > 0 ? (
        <div className="divide-y rounded-xl border">
          {skills.map((skill) => (
            <div key={skill.name} className="group p-3">
              <div className="flex items-start gap-2.5">
                <Puzzle className="text-kith/70 mt-0.5 size-4 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <button
                      type="button"
                      onClick={() => setOpen(open === skill.name ? null : skill.name)}
                      className="text-sm font-medium hover:underline"
                    >
                      {skill.name}
                    </button>
                    <span className="text-muted-foreground/60 font-mono text-[10px] tabular-nums">
                      {Math.round(skill.bodyChars / 3.7).toLocaleString()} tokens when read
                      {skill.resources.length > 0 ? ` · ${skill.resources.length} files` : ""}
                    </span>
                    {skill.license ? (
                      <span className="text-muted-foreground/50 text-[10px]">{skill.license}</span>
                    ) : null}
                  </div>
                  <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
                    {skill.description}
                  </p>

                  {skill.compatibility ? (
                    <p className="text-muted-foreground/70 mt-1 text-[11px]">
                      Needs: {skill.compatibility}
                    </p>
                  ) : null}

                  {/* A skill asking for pre-approved tools is worth seeing before it runs
                      anything, not after. */}
                  {skill.allowedTools.length > 0 ? (
                    <p className="text-muted-foreground/70 mt-1 flex items-start gap-1.5 text-[11px]">
                      <ShieldAlert className="mt-0.5 size-3 shrink-0 text-orange-400/80" />
                      <span>
                        Asks to use without prompting:{" "}
                        <span className="font-mono">{skill.allowedTools.join(", ")}</span>
                      </span>
                    </p>
                  ) : null}

                  {/* Fields from a newer or different tool. Said out loud so a skill that
                      half-works here is not a mystery. */}
                  {skill.unsupportedFields.length > 0 ? (
                    <p className="text-muted-foreground/50 mt-1 text-[11px]">
                      Expects features Kith doesn&rsquo;t have:{" "}
                      <span className="font-mono">{skill.unsupportedFields.join(", ")}</span>
                    </p>
                  ) : null}

                  {open === skill.name ? (
                    <div className="border-border/50 mt-2 border-t pt-2">
                      <p className="text-muted-foreground/60 mb-1 text-[10px] tracking-wide uppercase">
                        In its folder
                      </p>
                      <div className="flex flex-wrap gap-1">
                        {skill.resources.length === 0 ? (
                          <span className="text-muted-foreground/50 text-[11px]">
                            Just SKILL.md
                          </span>
                        ) : (
                          skill.resources.slice(0, 24).map((file) => (
                            <code
                              key={file}
                              className="bg-muted/60 rounded px-1.5 py-0.5 font-mono text-[10px]"
                            >
                              {file}
                            </code>
                          ))
                        )}
                        {skill.resources.length > 24 ? (
                          <span className="text-muted-foreground/50 text-[11px]">
                            +{skill.resources.length - 24} more
                          </span>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                </div>

                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={() => void openOnHost(skill.path, true)}
                    title="Show the folder in Finder"
                    className={cn(
                      "text-muted-foreground/40 hover:text-foreground rounded p-1 transition",
                      "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
                    )}
                  >
                    <FolderOpen className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => void drop(skill.name)}
                    disabled={busy}
                    title="Remove this skill"
                    aria-label={`Remove ${skill.name}`}
                    className={cn(
                      "text-muted-foreground/40 hover:text-destructive rounded p-1 transition",
                      "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
                    )}
                  >
                    <X className="size-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-muted-foreground rounded-xl border border-dashed p-6 text-center text-xs">
          <Puzzle className="mx-auto mb-2 size-5 opacity-40" />
          <p>
            Nothing yet, and nothing in his prompt about it either — an empty skill list costs him
            no context at all.
          </p>
          <p className="mt-1.5">
            Anthropic publishes a set at{" "}
            <span className="font-mono">github.com/anthropics/skills</span>. Clone it, then install
            a folder from <span className="font-mono">skills/</span>.
          </p>
        </div>
      )}

      <p className="text-muted-foreground/60 text-[11px] leading-relaxed">
        Installed in <code className="font-mono">{snapshot.root}</code>. A skill is instructions and
        code from somewhere else, so install ones you wrote or trust — read the folder first.
        Anything a skill has him run still goes through the same permission check as everything
        else.
      </p>

      {error ? <p className="text-destructive text-sm">{error}</p> : null}
    </div>
  );
}
