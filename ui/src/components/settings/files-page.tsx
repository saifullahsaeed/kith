import { FolderOpen, Lock } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  PathValue,
  SettingRow,
  SettingRows,
  SettingSection,
} from "@/components/settings/setting-row";
import { TuningPage } from "@/components/settings/tuning-page";
import { formatBytes } from "@/lib/bytes";
import { openOnHost } from "@/lib/files";
import type { ConfigPath, TunableGroup } from "@/lib/backend";

/**
 * Where his things live, and the settings about this machine.
 *
 * The folders come from `TuningSnapshot.paths`, which the server has been sending all along and
 * nothing rendered: every deployment path, what is in it, whether it is worth revealing and
 * whether it is pinned by an environment variable. "Where does he keep transcripts" was a
 * question you could only answer by reading the source, while the answer was arriving in a
 * payload the settings page already had.
 */
export function FilesPage({
  paths,
  groups,
  onSaved,
}: {
  paths: ConfigPath[];
  groups: TunableGroup[];
  onSaved: () => void;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-8 pt-1 pb-7">
      <div>
          <SettingSection
            title="Folders"
            sub="Where he keeps things on this computer."
          >
            <SettingRows>
              {paths.map((path) => (
                <SettingRow
                  key={path.value}
                  label={path.label}
                  help={
                    [
                      path.note,
                      // What is actually in it, where the server measured it — the number that
                      // turns "a folder exists" into "this is the one that is filling up".
                      path.entries !== undefined ? `${path.entries.toLocaleString()} files` : "",
                      path.bytes !== undefined ? formatBytes(path.bytes) : "",
                    ]
                      .filter(Boolean)
                      .join(" · ") || undefined
                  }
                  disabled={path.pinned}
                  disabledWhy={`Fixed by ${path.env} at launch, so it cannot be changed from here.`}
                >
                  <PathValue>{path.value}</PathValue>
                  {path.pinned ? (
                    <Lock className="text-muted-foreground/60 size-3.5" aria-label="Pinned by the environment" />
                  ) : null}
                  {path.open ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void openOnHost(path.value, true)}
                    >
                      <FolderOpen className="size-3.5" />
                      Reveal
                    </Button>
                  ) : null}
                </SettingRow>
              ))}
            </SettingRows>
        </SettingSection>

        {/* The `machine` tuning group — his clock, the Ollama address, the embedding model —
            under the folders, in the same measure and the same scroller. */}
        <TuningPage groups={groups} onSaved={onSaved} bare />
      </div>
    </div>
  );
}
