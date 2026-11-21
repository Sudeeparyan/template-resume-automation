import { describe, expect, it } from "vitest";
import { readTheme, saveTheme, THEME_KEY } from "./theme";

function memory(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (key: string) => (key in data ? data[key] : null),
    setItem: (key: string, value: string) => {
      data[key] = value;
    },
    removeItem: (key: string) => {
      delete data[key];
    },
  };
}

const blocked = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("SecurityError");
  },
  removeItem: () => {
    throw new Error("SecurityError");
  },
};

describe("theme choice", () => {
  it("defaults to the computer's setting", () => {
    expect(readTheme(memory())).toBe("system");
    expect(readTheme(null)).toBe("system");
  });
  it("reads only the two fixed choices", () => {
    expect(readTheme(memory({ [THEME_KEY]: "dark" }))).toBe("dark");
    expect(readTheme(memory({ [THEME_KEY]: "light" }))).toBe("light");
    expect(readTheme(memory({ [THEME_KEY]: "purple" }))).toBe("system");
  });
  it("falls back to the system theme when storage is blocked", () => {
    expect(readTheme(blocked)).toBe("system");
    expect(() => saveTheme("dark", blocked)).not.toThrow();
  });
  it("forgets a fixed choice when the system theme is picked", () => {
    const store = memory({ [THEME_KEY]: "dark" });
    saveTheme("system", store);
    expect(store.data[THEME_KEY]).toBeUndefined();
    saveTheme("light", store);
    expect(store.data[THEME_KEY]).toBe("light");
  });
});
