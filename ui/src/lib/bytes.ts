/**
 * A size, in the shortest form that is still true.
 *
 * One function, because there were three and they disagreed. `tool-result.tsx` had no gigabyte
 * tier, so a 2GB file read as "2048.0 MB"; `task-detail.tsx` had none either and switched
 * precision at 10MB; `advanced-tab.tsx` had both a GB tier and a zero case. Nothing was wrong
 * with any of them on the sizes each happened to see, which is exactly how three of them came to
 * exist — and why the same file could be described two ways on two screens.
 *
 * This is the advanced tab's version, which was the complete one.
 */
export function formatBytes(count: number): string {
  if (!count) return "0 B";
  if (count < 1024) return `${count} B`;
  if (count < 1024 * 1024) return `${(count / 1024).toFixed(0)} KB`;
  if (count < 1024 * 1024 * 1024) return `${(count / 1024 / 1024).toFixed(1)} MB`;
  return `${(count / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
