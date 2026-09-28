META = {
    "name": "migrate-sites",
    "description": "Make the same decided change at every place it applies: find the sites, change them in small batches in parallel, merge and check, verify nothing was missed.",
    "phases": ["Find", "Change", "Merge", "Verify"],
    "budget_usd": 8,
    "when_to_use": "one mechanical change repeats across many files — an API rename, a pattern replaced, a call migrated.",
    "args": "{change: what to do at each site, find: how to recognise a site (a grep pattern or a description), sites?: [paths], per_builder?: files per builder (default 3), tests?: true|false|filter}",
}

A = ARGS if isinstance(ARGS, dict) else {}
CHANGE = str(A.get("change") or ASKED).strip()
FIND = str(A.get("find") or "").strip()
PER = max(1, int(A.get("per_builder") or 3))
TESTS = A.get("tests", True)

with phase("Find"):
    sites = [str(one) for one in (A.get("sites") or [])]
    if not sites:
        sites = agent(
            f"Find every file where this change applies:\n{CHANGE}\n\n"
            + (f"How to recognise a site: {FIND}\n\n" if FIND else "")
            + "Search the whole repository; list each file once.",
            label="find the sites",
            schema={"files": ["str"]},
        ).get("files") or []
    batches = [sites[i : i + PER] for i in range(0, len(sites), PER)]
    log(f"{len(sites)} files in {len(batches)} batches")


def change(batch):
    built = agent(
        f"Make this change in exactly these files, and no others:\n{CHANGE}\n\nFiles:\n- "
        + "\n- ".join(batch)
        + "\n\nOther builders are doing the other files. Done means every site in these files is "
        "changed and check_code is clean on them.",
        kind="builder",
        label=", ".join(p.split("/")[-1] for p in batch)[:50],
    )
    if built is not None and not str(built.get("diff") or "").strip():
        built = revise(built, "Your patch is empty. Make the change in the files named now, or say why none of them need it.")
    return built


with phase("Change"):
    built = [b for b in parallel([lambda b=b: change(b) for b in batches]) if b and str(b.get("diff") or "").strip()]

if not built:
    finish({"error": "No builder produced a patch.", "sites": sites})
else:
    with phase("Merge"):
        merged = merge(*built, label="migrated", tests=TESTS)

    with phase("Verify"):
        left = agent(
            f"This change was meant to be made everywhere it applies:\n{CHANGE}\n\n"
            + (f"How to recognise a site: {FIND}\n\n" if FIND else "")
            + "Search the whole copy for sites that were missed or changed wrongly.",
            kind="verifier",
            label="anything missed",
            after=[merged],
            schema={"missed": ["path:line"], "wrong": ["str"]},
        )
        problems = (left.get("missed") or []) + (left.get("wrong") or [])
        final = merged
        if problems or not merged.get("clean"):
            fixed = revise(
                merged,
                "Finish the migration. "
                + ("Still to do or wrong:\n- " + "\n- ".join(str(p) for p in problems) if problems else "")
                + ("\nThe check or tests are failing: " + str(merged.get("check")) + str(merged.get("tests")) if not merged.get("clean") else ""),
                label="finish the migration",
            )
            final = merge(fixed, label="final", tests=TESTS)

    finish(
        {
            "patch": final.get("patch_file"),
            "clean": final.get("clean"),
            "sites": len(sites),
            "conflicts": final.get("conflicts"),
            "left_after_verify": problems,
        }
    )
