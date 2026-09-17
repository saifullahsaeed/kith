import { Check, ExternalLink, Loader2, RefreshCw, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  SettingRow,
  SettingRows,
  SettingSection,
} from "@/components/settings/setting-row";
import { useUpdate } from "@/components/shell/update-notice";

/**
 * What is running, and whether there is anything newer.
 *
 * This was a footer under the settings nav and nothing else — right for glancing at while
 * writing a bug report, and no use at all when the question is "am I behind, and what changed".
 * The footer stays, because a version visible on every page is worth more than a page you have
 * to go to; this is where the rest of the answer lives.
 */
export function UpdatesPage() {
  const { data, refetch, isFetching } = useUpdate();

  const running = data?.current || "";
  const checked = data?.checkedAt ? new Date(data.checkedAt).toLocaleString() : "";

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-8 pt-1 pb-7">
      {/* No cap. Every other tab spans the content area — see the note on the one left edge in
          `settings-page` — and a `max-w-4xl` here left this page ending partway across a pane the
          tab beside it filled. */}
      <div>
        <SettingSection title="This build">
          <SettingRows>
            <SettingRow
              label="Version"
              help={
                data && !data.packaged
                  ? "Running from a checkout, so there is no installed version to compare against."
                  : undefined
              }
            >
              <span className="font-mono text-[12.5px] tabular-nums">
                {running || "from source"}
              </span>
            </SettingRow>

            <SettingRow label="Last checked" help={data?.error ? data.error : undefined}>
              {checked ? (
                <span className="text-muted-foreground text-[11.5px]">{checked}</span>
              ) : null}
              <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
                {isFetching ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="size-3.5" />
                )}
                Check now
              </Button>
            </SettingRow>
          </SettingRows>
        </SettingSection>

        <SettingSection title="Status">
          <SettingRows>
            {/* Four answers, and they are deliberately four: "nothing to check" and "you are up
                to date" are different facts and must not read the same — someone running from a
                checkout should never be offered a download. */}
            {data?.error ? (
              <SettingRow
                label={
                  <span className="text-destructive flex items-center gap-2">
                    <TriangleAlert className="size-3.5" /> The check failed
                  </span>
                }
                help="What is running is still what is shown above; only the look for something newer failed."
              >
                <span />
              </SettingRow>
            ) : data && !data.packaged ? (
              <SettingRow
                label="Running from source"
                help="Nothing to update to — you are on whatever the checkout is at."
              >
                <span />
              </SettingRow>
            ) : data?.newer ? (
              <SettingRow
                label={`${data.latest} is out`}
                help={data.notes ? data.notes.split("\n")[0] : undefined}
              >
                {data.download ? (
                  <Button size="sm" asChild>
                    <a href={data.download}>Download</a>
                  </Button>
                ) : null}
                {data.page ? (
                  <Button variant="outline" size="sm" asChild>
                    <a href={data.page} target="_blank" rel="noreferrer">
                      <ExternalLink className="size-3.5" />
                      Release notes
                    </a>
                  </Button>
                ) : null}
              </SettingRow>
            ) : (
              <SettingRow label={
                <span className="text-roam flex items-center gap-2">
                  <Check className="size-3.5" /> Up to date
                </span>
              }>
                <span />
              </SettingRow>
            )}
          </SettingRows>
        </SettingSection>
      </div>
    </div>
  );
}
