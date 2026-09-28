META = {
    "name": "audit",
    "description": "Answer one question across several areas of a codebase at once, then ask what the sweep missed and cover that too.",
    "phases": ["Sweep", "Gaps", "Summary"],
    "budget_usd": 4,
    "when_to_use": "the question needs reading more code than one context should hold — an audit, an inventory, 'where does X happen'.",
    "args": "{question, areas: [folders or subsystems to cover], notes?}",
}

A = ARGS if isinstance(ARGS, dict) else {}
QUESTION = str(A.get("question") or ASKED).strip()
AREAS = [str(one) for one in (A.get("areas") or [])]
NOTES = str(A.get("notes") or "").strip()
FINDINGS = {"answer": "str", "findings": [{"where": "path:line", "what": "str"}], "not_checked": ["str"]}

if not AREAS:
    AREAS = agent(
        f"List the areas (top-level folders or subsystems) someone would have to read to answer this, "
        f"so the reading can be split between people:\n\n{QUESTION}",
        label="split the question",
        schema={"areas": ["str"]},
    ).get("areas") or []


def look(area):
    return agent(
        f"{QUESTION}\n\nCover only this area: {area}. Other people are covering the rest.\n"
        + (f"Notes: {NOTES}\n" if NOTES else "")
        + "Every finding needs an exact path:line. Say what you did not get to.",
        label=area[:50],
        schema=FINDINGS,
    )


with phase("Sweep"):
    swept = [r for r in parallel([lambda a=a: look(a) for a in AREAS]) if r]

with phase("Gaps"):
    gaps = agent(
        f"A sweep answered this question area by area:\n{QUESTION}\n\nAreas covered: {', '.join(AREAS)}\n"
        "What each said it did not check:\n- "
        + "\n- ".join(item for r in swept for item in r.get("not_checked") or [])
        + "\n\nName up to three areas the sweep missed — places the answer could also be — that are worth covering.",
        label="what was missed",
        schema={"areas": ["str"]},
    ).get("areas") or []
    extra = [r for r in parallel([lambda a=a: look(a) for a in gaps[:3]]) if r]

with phase("Summary"):
    everything = swept + extra
    summary = agent(
        f"Answer this from the findings below, citing path:line: {QUESTION}\n\n"
        + "\n\n".join(
            f"{r.get('answer')}\n" + "\n".join(f"- {f.get('where')}: {f.get('what')}" for f in r.get("findings") or [])
            for r in everything
        ),
        label="summary",
        model="strong",
    )

finish(
    {
        "answer": summary,
        "areas": AREAS + gaps[:3],
        "findings": [f for r in everything for f in r.get("findings") or []],
    }
)
