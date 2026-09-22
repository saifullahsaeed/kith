/**
 * The window that opens when the file isn't there.
 *
 * From a screenshot of a real session, and a second report of the same thing. A page opened
 * on `.kith/screens/home.html` and the whole window was black except for one line of red text
 * at the top:
 *
 *     there's no /Users/saifullahsaeed/Kith/trials/sprout/.kith/screens/home.html
 *
 * True, and useless in four separate ways: raw server prose, an absolute path nobody clicked
 * (the interface builds it — see `routes/workspace._anchor`), red for something that is not a
 * fault, and no mention of `today.html` sitting in that same folder.
 *
 * The state is not rare, either, which is the argument for treating it as a state. `linkTarget`
 * routes every path-shaped href to this viewer on purpose — the alternative was the browser
 * resolving it against the page origin and opening a second copy of Kith — and it accepts that
 * some of those paths will not exist. That trade is only honest if this is where they land.
 */
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FileViewer } from "@/components/files";
import type { Candidate } from "@/lib/backend/brain";
import { render, screen } from "@/test/render";

function open(error: string, candidates?: Candidate[], onPick?: (path: string) => void) {
  return render(
    <FileViewer
      open
      onOpenChange={() => {}}
      name=".kith/screens/home.html"
      content={null}
      error={error}
      candidates={candidates}
      onPick={onPick}
      projectId={null}
    />,
  );
}

const SYSTEMS: Candidate[] = [
  { name: "trials/sprout/.kith/design/system.json", path: "/Users/x/Kith/trials/sprout/.kith/design/system.json" },
  { name: "trials/oregon/.kith/design/system.json", path: "/Users/x/Kith/trials/oregon/.kith/design/system.json" },
];

describe("a file that isn't there", () => {
  it("says so as a state rather than as a red line", () => {
    open("there's no .kith/screens/home.html");
    expect(screen.getByText("There's no home.html")).toBeInTheDocument();
    // The exact string from the screenshot, which is what must not survive.
    expect(screen.queryByText(/there's no \.kith\/screens\/home\.html/)).not.toBeInTheDocument();
  });

  it("passes on what the folder does hold", () => {
    open("there's no .kith/screens/home.html — screens/ has today.html");
    expect(screen.getByText(/screens\/ has today\.html/)).toBeInTheDocument();
  });

  it("keeps the plain line for a refusal that is a question", () => {
    // "more than one report.md: …— say which one" is answerable, and flattening it into
    // "there's no home.html" would throw away the only useful half.
    const said = "there's more than one report.md: inbox/report.md, work/report.md — say which one";
    open(said);
    expect(screen.getByText(said)).toBeInTheDocument();
  });
});

describe("a name that matched several files", () => {
  /**
   * The second screenshot. Seven design systems, four of them rendered as the identical string
   * `.kith/design/system.json`, in one red line ending "say which one" — with nothing to say it
   * with. The path was written by him, not typed by anybody, so there was nothing to correct.
   */
  it("lists them instead of printing the sentence", () => {
    open("there's more than one system.json: … — say which one", SYSTEMS);
    expect(screen.getByText("trials/sprout/.kith/design/system.json")).toBeInTheDocument();
    expect(screen.getByText("trials/oregon/.kith/design/system.json")).toBeInTheDocument();
    expect(screen.queryByText(/say which one/)).not.toBeInTheDocument();
  });

  it("makes saying which one a click", () => {
    const pick = vi.fn();
    open("there's more than one system.json: … — say which one", SYSTEMS, pick);
    return userEvent
      .click(screen.getByRole("button", { name: /oregon/ }))
      .then(() => expect(pick).toHaveBeenCalledWith(SYSTEMS[1].path));
  });

  it("still says the plain thing when nothing came back to offer", () => {
    // An older server, or a refusal of some other kind that happens to read like this one.
    const said = "there's more than one system.json: a, b — say which one";
    open(said);
    expect(screen.getByText(said)).toBeInTheDocument();
  });
});
