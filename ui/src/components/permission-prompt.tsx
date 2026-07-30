import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { answerPermission, fetchPermissions, type PermissionRequest } from "@/lib/backend";

const VERB: Record<PermissionRequest["kind"], string> = {
  read: "read",
  write: "write to",
  delete: "delete",
  command: "run",
};

/**
 * He is asking for something, directly above where you would reply to him.
 *
 * This lived in a menu in the title bar for one build, and that was wrong twice over. It is
 * a question in a conversation, so it belongs in the conversation — next to the thing you
 * are reading, not behind a chevron you have to know to click. And a question you have not
 * noticed is a question that reads as him being stuck.
 *
 * Answering does not resume the turn. The turn already ended when the tool was refused; he
 * was told to ask and did. So allowing it grants the permission and stops there, and you
 * send the next message when you are ready — which also means "Allow" never silently sets
 * something running that you have not read yet.
 */
export function PermissionPrompt() {
  const [requests, setRequests] = useState<PermissionRequest[]>([]);
  const [remember, setRemember] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchPermissions()
        .then((state) => alive && setRequests(state.pending))
        .catch(() => {});
    load();
    const timer = setInterval(load, 2_500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (requests.length === 0) return null;
  const request = requests[0];

  const answer = (allow: boolean) => {
    setRequests((was) => was.filter((one) => one.id !== request.id));
    void answerPermission(request.id, allow, remember ? "always" : "session").catch(() => {});
    setRemember(false);
  };

  return (
    <div className="mx-auto mb-2 w-full max-w-(--thread-max-width) px-4">
      <div className="border-border/70 bg-card/80 rounded-xl border p-3 shadow-sm backdrop-blur">
        <div className="flex items-start gap-2.5">
          <ShieldAlert className="text-roam mt-0.5 size-4 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="text-sm">
              He wants to {VERB[request.kind]}{" "}
              {request.kind === "command" ? "a command" : "something outside his folder"}.
            </p>
            <code className="text-muted-foreground mt-0.5 block font-mono text-[11px] break-all">
              {request.what}
            </code>
            {requests.length > 1 ? (
              <p className="text-muted-foreground/70 mt-1 text-[11px]">
                {requests.length - 1} more after this one.
              </p>
            ) : null}
          </div>
        </div>

        <div className="mt-2.5 flex items-center gap-3">
          <label className="text-muted-foreground flex cursor-pointer items-center gap-1.5 text-[11px] select-none">
            <input
              type="checkbox"
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
              className="accent-kith size-3.5"
            />
            Don&apos;t ask again for this
          </label>
          <div className="flex-1" />
          <Button variant="ghost" size="sm" onClick={() => answer(false)}>
            Don&apos;t allow
          </Button>
          <Button size="sm" onClick={() => answer(true)}>
            Allow
          </Button>
        </div>
      </div>
    </div>
  );
}
