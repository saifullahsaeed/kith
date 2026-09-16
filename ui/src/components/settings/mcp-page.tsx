import { MCPServers } from "@/components/settings/mcp-servers";
import { TuningPage } from "@/components/settings/tuning-page";
import type { TunableGroup } from "@/lib/backend";

/**
 * Servers he calls tools on, and how long he waits for them.
 *
 * The list lived on the Tools page until now — under a heading about what he can reach, on a
 * page that never mentions MCP in its name or its hint. The two timeouts lived under Advanced.
 * Neither half was findable from the word you would search for, and they were three clicks
 * apart from each other.
 */
export function McpPage({ groups, onSaved }: { groups: TunableGroup[]; onSaved: () => void }) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-8 pt-1 pb-7">
      <div>
        <MCPServers />
        {/* Renamed: the server calls this group "Other programs\u2019 tools", which is a good
            heading on a page that is not already called MCP servers. */}
        <TuningPage groups={groups} onSaved={onSaved} bare rename={{ mcp: "How long he waits" }} />
      </div>
    </div>
  );
}
