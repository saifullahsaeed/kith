/**
 * What he may do on this machine, and what he is currently waiting to be allowed.
 *
 * Two places read this — the card above the composer and the badge in the title bar — and until
 * now each fetched it on its own timer, one every 2.5 seconds and one every 4. Forever, in an app
 * where a permission request is a rare event, because there was nothing to listen for: `permission`
 * was not a kind of change the server published. It is now, from every point the state actually
 * moves (a request opening, an answer, a mode change, a grant revoked), so this asks when something
 * has happened and not otherwise.
 *
 * One hook rather than a `useQuery` in each caller, because both callers also need to *write* —
 * and the write is where the interesting part is. Answering optimistically removes the request
 * before the server has confirmed it, which is not impatience: the click has to feel resolved, and
 * the round trip is a local socket away but not instant. The event that follows reconciles it.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  answerPermission,
  fetchPermissions,
  setPermissionMode,
  type PermissionMode,
  type PermissionRequest,
  type PermissionState,
} from "@/lib/backend";
import { keys } from "@/lib/query-keys";

export function usePermissions() {
  const cache = useQueryClient();
  const { data } = useQuery({
    queryKey: keys.permissions(),
    queryFn: fetchPermissions,
  });

  /** Drop a request from what is on screen now, without waiting to be told it is gone. */
  const forget = (requestId: string) =>
    cache.setQueryData<PermissionState>(keys.permissions(), (was) =>
      was ? { ...was, pending: was.pending.filter((one) => one.id !== requestId) } : was,
    );

  const answer = useMutation({
    mutationFn: ({
      requestId,
      allow,
      scope,
    }: {
      requestId: string;
      allow: boolean;
      scope: "session" | "always";
    }) => answerPermission(requestId, allow, scope),
    onMutate: ({ requestId }) => forget(requestId),
    // No `onSuccess` refetch: approving publishes a `permission` change, so the invalidation is
    // already on its way. Refetching here as well would be the same request twice.
    onError: () => void cache.invalidateQueries({ queryKey: keys.permissions() }),
  });

  const mode = useMutation({
    mutationFn: (next: PermissionMode) => setPermissionMode(next),
    onMutate: (next) =>
      cache.setQueryData<PermissionState>(keys.permissions(), (was) =>
        was ? { ...was, mode: next } : was,
      ),
    onError: () => void cache.invalidateQueries({ queryKey: keys.permissions() }),
  });

  return {
    /** "ask" until the first answer arrives — the same default the pickers assumed before. */
    mode: data?.mode ?? ("ask" as PermissionMode),
    pending: (data?.pending ?? []) as PermissionRequest[],
    grants: data?.grants ?? [],
    sessionGrants: data?.sessionGrants ?? [],
    workspace: data?.workspace,
    answer: (requestId: string, allow: boolean, scope: "session" | "always") =>
      answer.mutate({ requestId, allow, scope }),
    setMode: (next: PermissionMode) => mode.mutate(next),
  };
}
