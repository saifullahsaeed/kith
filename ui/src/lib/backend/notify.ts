/** Client for how much he may interrupt you. */

export interface NotifyOption {
  value: string;
  label: string;
  hint: string;
}

export interface NotifyState {
  level: string;
  levels: NotifyOption[];
}

export async function fetchNotifyLevel(): Promise<NotifyState> {
  const response = await fetch("/api/notify");
  if (!response.ok) throw new Error(`/api/notify returned ${response.status}`);
  return (await response.json()) as NotifyState;
}

export async function setNotifyLevel(level: string): Promise<NotifyState> {
  const response = await fetch("/api/notify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ level }),
  });
  if (!response.ok) throw new Error(`could not set that (${response.status})`);
  return (await response.json()) as NotifyState;
}
