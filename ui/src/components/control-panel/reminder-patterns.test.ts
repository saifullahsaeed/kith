import { describe, expect, it, vi } from "vitest";

import {
  dayPreview,
  daySummary,
  foldByDay,
  kindOf,
  patterns,
  repeatSummary,
  suggestSchedules,
  taskRefs,
  typicalClock,
  type ReminderLike,
} from "./reminder-patterns";

// The day a reminder falls on is a local idea, and these are his real reminders as his zone saw
// them — pinned, so the folds below hold wherever the suite runs.
vi.stubEnv("TZ", "Asia/Riyadh");

/** His 24 real reminders, as `GET /api/brain` returned them on 2026-10-01 (all fired). */
const REAL: ReminderLike[] = [
  { id: 1, status: "done", fire_at: "2026-08-06T04:48:05.270908+00:00", created_at: "2026-08-06T04:43:05.270930+00:00", conversation_id: null,
    note: "Check GitHub Actions deploy runs for Sadeef/sadeef-ai on main, especially run 31071981307 for commit 3dcc3d7, and report the result or inspect failure logs." },
  { id: 2, status: "done", fire_at: "2026-08-06T06:01:02.022444+00:00", created_at: "2026-08-06T05:56:02.022491+00:00", conversation_id: null,
    note: "Check GitHub Actions deploy run 31075555985 for Sadeef/sadeef-ai (commit b432a34) and report the final status on task #79." },
  { id: 3, status: "done", fire_at: "2026-08-06T10:06:01.065388+00:00", created_at: "2026-08-06T10:01:01.065418+00:00", conversation_id: null,
    note: "Check the full backend pytest run for Task #74 after the runtime_context extraction, then report the result before any commit." },
  { id: 4, status: "done", fire_at: "2026-08-06T10:12:54.428940+00:00", created_at: "2026-08-06T10:07:54.429006+00:00", conversation_id: null,
    note: "Re-check the full backend pytest run for Task #74; report the final result before any commit." },
  { id: 5, status: "done", fire_at: "2026-08-06T10:19:46.738753+00:00", created_at: "2026-08-06T10:14:46.738777+00:00", conversation_id: null,
    note: "Re-check the full backend pytest run for Task #74, report the final pass/fail result before any commit." },
  { id: 6, status: "done", fire_at: "2026-08-06T11:13:20.530246+00:00", created_at: "2026-08-06T11:08:20.530278+00:00", conversation_id: null,
    note: "Check the in-progress full backend pytest run for Task #74. If it passes, review current diff, commit the agent runtime extraction, and run/monitor CI. If it fails, fix the failure first." },
  { id: 7, status: "done", fire_at: "2026-08-06T11:21:32.511439+00:00", created_at: "2026-08-06T11:16:32.511522+00:00", conversation_id: null,
    note: "Re-check the fresh full backend pytest run for Task #74. If it passes, run final diff/static review, commit the agent runtime extraction, push main, and monitor GitHub Actions. If it fails, fix the failure first." },
  { id: 8, status: "done", fire_at: "2026-08-06T11:28:25.646372+00:00", created_at: "2026-08-06T11:23:25.646428+00:00", conversation_id: null,
    note: "Re-check Task #74's re-attached full backend pytest run. If it has a final pass, run final diff/static review, commit the agent runtime extraction, push main, and monitor GitHub Actions; if it fails, fix the failure first." },
  { id: 9, status: "done", fire_at: "2026-08-06T11:42:48.856193+00:00", created_at: "2026-08-06T11:37:48.856225+00:00", conversation_id: null,
    note: "Re-check the restarted full backend pytest run for Task #74. It is the final verification after restoring the conductor compatibility export and Ruff cleanup; if green, update checklist item 232 and prepare the commit." },
  { id: 10, status: "done", fire_at: "2026-08-06T12:01:57.555355+00:00", created_at: "2026-08-06T11:56:57.555380+00:00", conversation_id: "20260803-110800342-734b7a",
    note: "Check the still-running full backend pytest for Task #74. Do not clear checklist item 232 or commit until the corrected run returns its final result." },
  { id: 16, status: "done", fire_at: "2026-08-07T16:07:18.362742+00:00", created_at: "2026-08-07T15:59:18.362768+00:00", conversation_id: "20260806-202705391-8b7141",
    note: "Collect Task #76 final backend/frontend processes. If both green, update checklist/evidence/memory, commit, push origin/main, and mark Task #76 done." },
  { id: 18, status: "done", fire_at: "2026-08-08T18:54:56.208986+00:00", created_at: "2026-08-08T18:46:56.209126+00:00", conversation_id: "20260806-202705391-8b7141",
    note: "Collect Task #78 full backend/frontend gates. If green, complete checklist/evidence/memory, commit/push, and close the final milestone cleanup task." },
  { id: 19, status: "done", fire_at: "2026-08-08T19:06:45.133356+00:00", created_at: "2026-08-08T18:58:45.133427+00:00", conversation_id: "20260806-202705391-8b7141",
    note: "Collect rerun Task #78 backend-full-task78b and frontend-tests-task78b after intermittent frontend timeouts; if both green, run/confirm build as already green, finish task checklist/evidence, commit and push." },
  { id: 20, status: "done", fire_at: "2026-08-08T19:14:20.014211+00:00", created_at: "2026-08-08T19:09:20.014294+00:00", conversation_id: "20260806-202705391-8b7141",
    note: "Collect backend-final-task78 after latest calls.py cleanup. If green (frontend already 72/593 + build green), update Task #78 checklist/evidence/memory, commit and push; if red, fix before closing." },
  { id: 21, status: "done", fire_at: "2026-08-08T19:26:43.684795+00:00", created_at: "2026-08-08T19:18:43.684823+00:00", conversation_id: "20260806-202705391-8b7141",
    note: "Collect final Task #78 calls.py cleanup backend/frontend processes. If green, update checklist/evidence/memory, commit/push, and close Task #78. Frontend 72/593 + build previously green with 10s timeout; latest rerun is final evidence." },
  { id: 27, status: "done", fire_at: "2026-08-10T08:59:48.675202+00:00", created_at: "2026-08-10T08:51:48.675232+00:00", conversation_id: "20260806-202705391-8b7141",
    note: "Collect backend-after-extensions full suite; if green, push 05dc773, update Task #86 checklist/evidence, and leave task in review for approval/close. Frontend 73/597 + TypeScript/build are green." },
  { id: 28, status: "done", fire_at: "2026-08-11T01:23:11.972533+00:00", created_at: "2026-08-11T01:15:11.972562+00:00", conversation_id: "20260806-202705391-8b7141",
    note: "Collect Task #88 full backend/frontend gates. If green, record final evidence, commit/push connector foundation, and move Task #88 to review; if red, fix root cause before close." },
  { id: 29, status: "done", fire_at: "2026-08-11T01:24:40.362855+00:00", created_at: "2026-08-11T01:16:40.362880+00:00", conversation_id: "20260806-202705391-8b7141",
    note: "Collect Task #88 full backend after connector foundation. Frontend tests/build are green; if backend green, run/confirm 628 baseline/static, update plan/checklist, commit/push, and move task to review." },
  { id: 33, status: "done", fire_at: "2026-08-11T05:14:10.055697+00:00", created_at: "2026-08-11T05:06:10.055722+00:00", conversation_id: "20260806-202705391-8b7141",
    note: "Collect Task #90 runtime full backend. If green, rerun 628 baseline/static, update plan/checklist, commit/push runtime resolution checkpoint; if red, investigate root cause." },
  { id: 38, status: "done", fire_at: "2026-08-12T17:47:34.491142+00:00", created_at: "2026-08-12T17:43:34.491190+00:00", conversation_id: "20260811-204628168-ab0e8d",
    note: "Re-check the full Sadeef AI pytest run for the user's request to fix failing tests, then report failures or push any needed fix." },
  { id: 39, status: "done", fire_at: "2026-08-12T18:23:37.932605+00:00", created_at: "2026-08-12T18:20:37.932637+00:00", conversation_id: "20260811-204628168-ab0e8d",
    note: "Check the local full pytest run for Sadeef AI; fix every failure before pushing." },
  { id: 40, status: "done", fire_at: "2026-08-12T19:27:43.845966+00:00", created_at: "2026-08-12T19:19:43.845994+00:00", conversation_id: "20260811-204628168-ab0e8d",
    note: "Check Task #99 progress: GitHub Actions run 31631887536 for commit 57ba026, then run the full local suite if CI is green and report the task status." },
  { id: 41, status: "done", fire_at: "2026-08-12T19:35:29.368917+00:00", created_at: "2026-08-12T19:28:29.368944+00:00", conversation_id: "20260811-204628168-ab0e8d",
    note: "Check Task #99 full local pytest run and GitHub Actions run 31631887536; report whether CI/deploy is green and update checkpoint count." },
  { id: 44, status: "done", fire_at: "2026-08-30T06:30:00+00:00", created_at: "2026-08-30T02:02:12.452766+00:00", conversation_id: "20260830-045849343-35788a",
    note: "Alarm — 9:30 AM Saudi time (Mac will also blast Nyan Cat via alarm-930 process)" },
];

const SADEEF_CHATS = new Set(["20260806-202705391-8b7141", "20260811-204628168-ab0e8d", "20260803-110800342-734b7a"]);
const projectFor = (conversationId: string) => (SADEEF_CHATS.has(conversationId) ? "Sadeef AI" : undefined);

describe("kindOf", () => {
  it("reads what a note is mostly about from its first clause", () => {
    expect(kindOf("Re-check the fresh full backend pytest run for Task #74. If it passes, … monitor GitHub Actions.").key).toBe("tests");
    expect(kindOf("Check Task #99 full local pytest run and GitHub Actions run 31631887536; report whether CI is green.").key).toBe("ci");
    expect(kindOf("Collect Task #88 full backend/frontend gates. If green, record final evidence.").key).toBe("gates");
    expect(kindOf("Alarm — 9:30 AM Saudi time").key).toBe("alarm");
  });

  it("keys anything else by its leading words, ignoring numbers and filler", () => {
    const a = kindOf("Ask the landlord about the boiler on Monday");
    const b = kindOf("Ask landlord about boiler again");
    expect(a.key).toBe(b.key);
    expect(a.key.startsWith("lead:")).toBe(true);
    expect(kindOf("Water the plants").key).not.toBe(a.key);
  });
});

describe("patterns, on his real reminders", () => {
  it("finds the four kinds the 24 fall into", () => {
    expect(patterns(REAL).map((group) => [group.kind.label, group.items.length])).toEqual([
      ["Re-check a running pytest suite", 10],
      ["Collect backend and frontend test gates", 9],
      ["Check a GitHub Actions run", 4],
      ["An alarm", 1],
    ]);
  });

  it("says what repeats, and how far ahead each was set", () => {
    const summary = repeatSummary(REAL);
    expect(summary?.repeated).toBe(23);
    expect(summary?.total).toBe(24);
    expect(summary?.sentence).toBe(
      "were him coming back to a test or CI run he had started — each one set 3 to 8 minutes ahead.",
    );
    expect(summary?.kinds.map((kind) => kind.n)).toEqual([10, 9, 4, 1]);
  });

  it("has nothing to say when nothing repeats", () => {
    expect(repeatSummary(REAL.slice(-1))).toBeNull();
    expect(repeatSummary([])).toBeNull();
  });
});

describe("foldByDay", () => {
  const days = foldByDay(REAL);

  it("folds Past into days, newest first", () => {
    expect(days.map((day) => `${day.date} ${day.dow} · ${day.items.length}`)).toEqual([
      "Aug 30 Sun · 1",
      "Aug 12 Wed · 4",
      "Aug 11 Tue · 3",
      "Aug 10 Mon · 1",
      "Aug 8 Sat · 4",
      "Aug 7 Fri · 1",
      "Aug 6 Thu · 10",
    ]);
  });

  it("summarises each day the way the board does", () => {
    const aug12 = days[1];
    expect(aug12.summary).toBe("4 reminders · all fired");
    expect(aug12.span).toBe("8:47 – 10:35 PM");
    expect(aug12.items.map((item) => item.id)).toEqual([41, 40, 39, 38]);
    expect(days[0].summary).toBe("1 reminder · fired");
    expect(days[0].span).toBe("9:30 AM");
    expect(days[6].span).toBe("7:48 AM – 3:01 PM");
  });

  it("says what a day was about", () => {
    expect(days[0].preview).toBe("Alarm — 9:30 AM Saudi time (Mac will also blast Nyan Cat via alarm-930 process)");
    expect(days[6].preview).toBe("8 pytest re-checks, 2 GitHub Actions checks · Tasks #74 and #79");
    expect(days[4].preview).toBe("4 test-gate collections · Task #78");
  });

  it("counts mixed outcomes", () => {
    const mixed = REAL.slice(0, 3).map((item, i) => ({ ...item, status: i === 0 ? "cancelled" : "done" }));
    expect(daySummary(mixed)).toBe("3 reminders · 1 cancelled, 2 fired");
    expect(dayPreview(mixed.slice(0, 1))).toBe(mixed[0].note);
  });
});

describe("taskRefs", () => {
  it("names the tasks, briefly", () => {
    expect(taskRefs(["Task #74 run"])).toBe("Task #74");
    expect(taskRefs(["Task #88 gates", "Task #90 runtime", "Task #88 again"])).toBe("Tasks #88 and #90");
    expect(taskRefs(["#1", "#2", "#3", "#4"])).toBe("Tasks #1, #2, #3 and 1 more");
    expect(taskRefs(["nothing here"])).toBe("");
  });
});

describe("suggestSchedules", () => {
  it("turns each repeated kind, and the alarm, into a daily job", () => {
    const suggestions = suggestSchedules(REAL, [], projectFor);
    expect(suggestions.map((s) => [s.key, s.dailyAt, s.count])).toEqual([
      ["tests", "14:30", 10],
      ["gates", "19:00", 9],
      ["ci", "15:30", 4],
      ["alarm", "09:30", 1],
    ]);
    const [tests, gates, ci, alarm] = suggestions;
    expect(tests.note).toBe("Run the full Sadeef AI backend suite and report any failures");
    // The shortest of them: the quickest to read, and the most typical.
    expect(tests.quote).toBe("Check the local full pytest run for Sadeef AI; fix every failure before pushing.");
    expect(tests.basis).toBe("Based on 10 reminders · Aug 6 and Aug 12");
    expect(tests.conversationId).toBe("20260811-204628168-ab0e8d");
    expect(gates.basis).toBe("Based on 9 reminders · Aug 7 to Aug 11");
    expect(ci.note).toBe("Check GitHub Actions on Sadeef/sadeef-ai and report anything red");
    expect(alarm.note).toBe("Sound the 9:30 AM alarm");
    expect(alarm.basis).toBe("Set once on Aug 30");
  });

  it("retires a suggestion once a schedule covers it", () => {
    const keys = suggestSchedules(REAL, [{ note: "Check GitHub Actions on Sadeef/sadeef-ai and report anything red" }]).map(
      (s) => s.key,
    );
    expect(keys).toEqual(["tests", "gates", "alarm"]);
  });

  it("suggests nothing from one-off notes", () => {
    expect(suggestSchedules([{ ...REAL[0], note: "Water the plants" }], [])).toEqual([]);
  });
});

describe("typicalClock", () => {
  it("is the median time of day, to the half hour", () => {
    expect(typicalClock(["2026-08-06T05:10:00+03:00", "2026-08-07T05:20:00+03:00", "2026-08-08T09:50:00+03:00"])).toBe("05:30");
    expect(typicalClock([])).toBe("09:00");
  });
});
