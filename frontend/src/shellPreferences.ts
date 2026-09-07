const SIDEBAR_PREFERENCE_KEY = "smartops.sidebar.collapsed.v1";

export function loadSidebarCollapsed(storage: Storage = window.localStorage): boolean {
  try { return storage.getItem(SIDEBAR_PREFERENCE_KEY) === "true"; } catch { return false; }
}

export function saveSidebarCollapsed(collapsed: boolean, storage: Storage = window.localStorage): void {
  try { storage.setItem(SIDEBAR_PREFERENCE_KEY, String(collapsed)); } catch { /* Session state remains usable when storage is blocked. */ }
}

export { SIDEBAR_PREFERENCE_KEY };
