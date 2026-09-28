META = {
    "name": "review-changes",
    "description": "Review the uncommitted changes (or a patch) from several angles, then try to refute every finding; only what survives comes back.",
    "phases": ["Review", "Refute"],
    "budget_usd": 4,
    "when_to_use": "before calling a change done, or when they ask for a review.",
    "args": "{patch?: path to a .patch to review instead of the uncommitted changes, focus?: what matters most, lenses?: [str]}",
}

A = ARGS if isinstance(ARGS, dict) else {}
FOCUS = str(A.get("focus") or "").strip()
PATCH = str(A.get("patch") or "").strip()
LENSES = [str(one) for one in (A.get("lenses") or [])] or [
    "correctness: logic errors, wrong conditions, broken edge cases, crashes",
    "completeness: anything the person asked for that the change does not do, or does only partly",
    "regressions: behaviour or callers elsewhere that this change breaks",
]
FINDINGS = {"findings": [{"where": "path:line", "problem": "str", "why_it_matters": "str"}]}
VERDICT = {"real": "bool", "reason": "str"}

WHAT = (
    f"the patch at {PATCH} (read it)" if PATCH else "the uncommitted changes in this repository (use `changes` to see them)"
)

with phase("Review"):
    found = parallel(
        [
            lambda lens=lens: agent(
                f"Review {WHAT} through one lens only — {lens}.\n\n"
                + (f"What matters most: {FOCUS}\n\n" if FOCUS else "")
                + (f"What the person asked for:\n{ASKED}\n\n" if ASKED else "")
                + "Report only real problems, each with the exact path:line. No style nits.",
                label=lens.split(":")[0],
                schema=FINDINGS,
                model="strong",
            )
            for lens in LENSES
        ]
    )
    seen = set()
    findings = []
    for batch in found:
        for one in (batch or {}).get("findings") or []:
            key = (str(one.get("where")), str(one.get("problem"))[:80])
            if key not in seen:
                seen.add(key)
                findings.append(one)
    log(f"{len(findings)} findings to test")

with phase("Refute"):

    def survives(one):
        votes = [
            v
            for v in parallel(
                [
                    lambda i=i: agent(
                        f"Try to refute this review finding about {WHAT}. Read the code yourself. Default "
                        "to real=false if you cannot confirm it from the code.\n\n"
                        f"Where: {one.get('where')}\nProblem: {one.get('problem')}\nWhy it matters: {one.get('why_it_matters')}",
                        label=f"refute {str(one.get('where'))[:40]} ({i + 1})",
                        schema=VERDICT,
                    )
                    for i in range(2)
                ]
            )
            if v
        ]
        real = [v for v in votes if v.get("real") is True]
        return {**one, "confirmed_by": len(real), "reasons": [v.get("reason") for v in votes]} if len(real) == len(votes) and votes else None

    kept = [f for f in parallel([lambda one=one: survives(one) for one in findings]) if f]

finish({"confirmed": kept, "raised": len(findings), "dropped": len(findings) - len(kept)})
