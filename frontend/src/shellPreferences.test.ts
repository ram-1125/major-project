import { describe, expect, it } from "vitest";

import { loadSidebarCollapsed, saveSidebarCollapsed, SIDEBAR_PREFERENCE_KEY } from "./shellPreferences";

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const values = new Map(Object.entries(initial));
  return {
    get length() { return values.size; },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => { values.delete(key); },
    setItem: (key, value) => { values.set(key, value); },
  };
}

describe("sidebar preference", () => {
  it("persists a validated boolean representation", () => {
    const storage = memoryStorage();
    saveSidebarCollapsed(true, storage);
    expect(storage.getItem(SIDEBAR_PREFERENCE_KEY)).toBe("true");
    expect(loadSidebarCollapsed(storage)).toBe(true);
    saveSidebarCollapsed(false, storage);
    expect(loadSidebarCollapsed(storage)).toBe(false);
  });

  it("falls back safely for invalid or blocked storage", () => {
    expect(loadSidebarCollapsed(memoryStorage({ [SIDEBAR_PREFERENCE_KEY]: "yes" }))).toBe(false);
    const blocked = memoryStorage();
    blocked.getItem = () => { throw new Error("blocked"); };
    expect(loadSidebarCollapsed(blocked)).toBe(false);
  });
});
