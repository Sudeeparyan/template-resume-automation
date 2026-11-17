import { describe, expect, it } from "vitest";
import { LatestRequest } from "./latestRequest";

function pending<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe("overlapping workspace reads", () => {
  it("keeps the newer empty profile list when an earlier pre-reset response arrives later", async () => {
    const reads = new LatestRequest();
    const older = pending<string[]>();
    const newer = pending<string[]>();
    let profiles: string[] = [];
    const first = reads.run(() => older.promise, (value) => { profiles = value; });
    const second = reads.run(() => newer.promise, (value) => { profiles = value; });
    newer.resolve([]);
    await second;
    older.resolve(["removed-profile"]);
    await first;
    expect(profiles).toEqual([]);
  });

  it("ignores a disposed profile's pending snapshot and pending error", async () => {
    const reads = new LatestRequest();
    const snapshot = pending<string>();
    const applied: string[] = [];
    const result = reads.run(() => snapshot.promise, (value) => applied.push(value));
    reads.invalidate();
    snapshot.resolve("old private summary");
    await result;
    expect(applied).toEqual([]);
    const failure = pending<string>();
    const failed = reads.run(() => failure.promise, (value) => applied.push(value));
    reads.invalidate();
    failure.reject(new Error("Old profile unavailable"));
    await expect(failed).resolves.toBeUndefined();
  });

  it("preserves the newest snapshot when an older request fails, and reports current failures", async () => {
    const reads = new LatestRequest();
    const old = pending<string>();
    let summary = "";
    const first = reads.run(() => old.promise, (value) => { summary = value; });
    await reads.run(async () => "current summary", (value) => { summary = value; });
    old.reject(new Error("Old failure"));
    await expect(first).resolves.toBeUndefined();
    expect(summary).toBe("current summary");
    await expect(reads.run(async () => { throw new Error("Current failure"); }, () => {})).rejects.toThrow("Current failure");
  });
});
