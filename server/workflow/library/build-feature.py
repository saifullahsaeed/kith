META = {
    "name": "build-feature",
    "description": "Build a decided change that splits into parts: shared contract first, parts in parallel, merged and checked in one copy, verified against what they asked, fixed once.",
    "phases": ["Plan", "Contract", "Build", "Merge", "Verify", "Fix"],
    "budget_usd": 8,
    "when_to_use": "the change is decided and touches several independent places — screens, endpoints, modules.",
    "args": "{goal, parts?: [{name, files: [paths], change}], contract?: what the parts share, tests?: true|false|filter, notes?: what every builder should know}",
}

A = ARGS if isinstance(ARGS, dict) else {}
GOAL = str(A.get("goal") or ASKED).strip()
NOTES = str(A.get("notes") or "").strip()
TESTS = A.get("tests", True)
PART = {"name": "str", "files": ["str"], "change": "str"}
VERDICT = {"verdict": "met | not met | partly met", "missing": ["str"], "wrong": ["str"]}


def spelled(part):
    files = ", ".join(part.get("files") or []) or "(find them)"
    return f"{part.get('name')}: {part.get('change')} Files: {files}"


parts = [p for p in (A.get("parts") or []) if isinstance(p, dict)]
contract_brief = str(A.get("contract") or "").strip()

with phase("Plan"):
    if not parts:
        plan = agent(
            f"Plan how to build this as independent parts that separate builders can each finish.\n\nGoal: {GOAL}\n\n"
            + (f"Notes: {NOTES}\n\n" if NOTES else "")
            + "Read enough of the code to name real files. Each part is one screen, one endpoint or one "
            "module — a few files a builder can change without waiting for another part. Also say what "
            "the parts must share (types, function signatures, route or record shapes) so it can be "
            "written once, first; leave contract empty if they share nothing.",
            label="plan the parts",
            schema={"parts": [PART], "contract": "str"},
            model="strong",
        )
        parts = [p for p in (plan.get("parts") or []) if isinstance(p, dict)]
        contract_brief = contract_brief or str(plan.get("contract") or "").strip()
    log(f"{len(parts)} parts")

if not parts:
    finish({"error": "No parts to build — the plan found none.", "goal": GOAL})
else:
    contract = None
    if contract_brief:
        with phase("Contract"):
            contract = agent(
                "Write ONLY the shared contract the parts below will all build against — types, "
                "function signatures, route and record shapes, with stub bodies where needed — so "
                "that separate builders cannot disagree on an interface. Do not build the parts.\n\n"
                f"Goal: {GOAL}\n\nThe contract: {contract_brief}\n\nThe parts that will use it:\n- "
                + "\n- ".join(spelled(p) for p in parts),
                kind="builder",
                label="contract",
            )
            if contract is not None and not str(contract.get("diff") or "").strip():
                contract = revise(contract, "Your patch is empty. Write the shared contract files now, even as stubs.")
    base = [contract] if contract and str(contract.get("diff") or "").strip() else []

    def build(part):
        built = agent(
            f"Make this one part of a larger change, and only this part.\n\nThe whole change: {GOAL}\n\n"
            f"Your part: {part.get('change')}\nFiles: {', '.join(part.get('files') or []) or 'find them'}\n\n"
            + ("The shared contract is already in your copy — build against it exactly, do not redefine it.\n\n" if base else "")
            + (f"Notes: {NOTES}\n\n" if NOTES else "")
            + "Other builders are making the other parts at the same time; do not touch their files. "
            "Done means your files are changed, check_code is clean on them, and the nearest tests pass.",
            kind="builder",
            label=str(part.get("name") or "part"),
            after=base,
        )
        if built is not None and not str(built.get("diff") or "").strip():
            built = revise(
                built,
                "Your patch is empty — nothing was changed in your copy. If the part really needs no "
                "change, say why. Otherwise make the edits now, starting with the files named.",
            )
        return built

    with phase("Build"):
        built = [b for b in parallel([lambda p=p: build(p) for p in parts]) if b and str(b.get("diff") or "").strip()]
    log(f"{len(built)} of {len(parts)} parts came back with a patch")

    if not built:
        finish({"error": "No builder produced a patch.", "parts": [p.get("name") for p in parts]})
    else:
        with phase("Merge"):
            merged = merge(*(base + built), label="merged", tests=TESTS)
            if not merged.get("clean"):
                found = []
                if merged.get("conflicts"):
                    found.append("These patches did not apply together: " + "; ".join(f"{c['label']}: {c['why']}" for c in merged["conflicts"]))
                for check in merged.get("check") or []:
                    if check.get("clean") is False:
                        found.append(f"The check failed in {check['root']}:\n{str(check.get('problems') or '')[:3000]}")
                for run in merged.get("tests") or []:
                    if run.get("ok") is False:
                        found.append(f"Tests failed in {run['root']}: {run.get('failures') or run.get('error') or ''}\n{str(run.get('log') or '')[-2000:]}")
                fixed = revise(
                    merged,
                    "The parts are applied together in your copy and do not work yet. Fix what is below — "
                    "the seams between parts first — without undoing any part:\n\n" + "\n\n".join(found),
                    label="fix the merge",
                )
                merged = merge(fixed, label="merged (fixed)", tests=TESTS)

        with phase("Verify"):
            votes = [
                v
                for v in parallel(
                    [
                        lambda i=i: agent(
                            f"Verify this change (verifier {i + 1} of 3, working independently). The goal as "
                            f"written for the builders: {GOAL}\n\nCheck every part of what the person asked "
                            "is really there and works: read the changed code and run the tests.",
                            kind="verifier",
                            label=f"verify {i + 1}",
                            after=[merged],
                            schema=VERDICT,
                        )
                        for i in range(3)
                    ]
                )
                if v
            ]
            raised = [f"- {item}" for v in votes for item in (v.get("missing") or []) + (v.get("wrong") or [])]
            agreed = []
            if raised:
                tally = agent(
                    "Three verifiers checked the same change independently. Keep only the problems that at "
                    "least two of them raised (in any wording), merged into one list; drop the rest.\n\n"
                    + "\n".join(raised),
                    label="reconcile the verifiers",
                    schema={"agreed": ["str"]},
                )
                agreed = [str(one) for one in tally.get("agreed") or []]

        final = merged
        if agreed:
            with phase("Fix"):
                fixed = revise(
                    merged,
                    "Independent verifiers agree these are missing or wrong. Confirm each one yourself "
                    "before changing anything, fix the ones that are real, and say which you did not fix "
                    "and why:\n- " + "\n- ".join(agreed),
                    label="fix what verification found",
                )
                final = merge(fixed, label="final", tests=TESTS)

        finish(
            {
                "patch": final.get("patch_file"),
                "clean": final.get("clean"),
                "check": final.get("check"),
                "tests": [{k: t.get(k) for k in ("root", "ok", "passed", "failed", "failures")} for t in final.get("tests") or []],
                "conflicts": final.get("conflicts"),
                "parts": [b.get("label") for b in built],
                "verdicts": [v.get("verdict") for v in votes],
                "agreed_problems": agreed,
                "fixed_after_verify": bool(agreed),
            }
        )
