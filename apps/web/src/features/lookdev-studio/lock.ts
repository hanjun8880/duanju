export const MAX_LOCKED = 3;

/** Click a candidate: locked -> unlock it; unlocked -> lock it, unless 3 are already locked (returns null). */
export function toggleLock(locked: string[], url: string, max = MAX_LOCKED): string[] | null {
  if (locked.includes(url)) return locked.filter((u) => u !== url);
  if (locked.length >= max) return null;
  return [...locked, url];
}
