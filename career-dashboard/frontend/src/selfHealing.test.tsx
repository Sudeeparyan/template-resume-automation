import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api";
import { REMOVAL_REASONS, RemoveJobDialog, removalText } from "./features/RemoveJob";
import { MorningJobsRow } from "./profiles";
import type { Job } from "./types";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("reads mend themselves", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("waits and retries a read while the app restarts", async () => {
    vi.useFakeTimers();
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(new Response("<html>Bad gateway</html>", { status: 502 }))
      .mockResolvedValueOnce(json(200, { ok: true }));
    const result = api("/v2/hunt");
    await vi.runAllTimersAsync();
    await expect(result).resolves.toEqual({ ok: true });
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("never repeats a write, and says plainly what happened", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api("/v2/hunt/run", "POST", {})).rejects.toThrow("may be restarting");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does not retry the app's own answer to a bad request", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(json(400, { detail: "Choose between 1 and 40 jobs to find." }));
    await expect(api("/v2/hunt")).rejects.toThrow("Choose between 1 and 40 jobs");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe("removing a job teaches the next searches", () => {
  it("offers the reasons and sends the chosen one with the person's own words", () => {
    expect(removalText("company", "")).toBe("Not this company");
    expect(removalText("role", "  mostly sales ")).toBe("Wrong kind of role: mostly sales");
    const job = { id: "j1", company: "Acme", title: "Sales Analyst" } as Job;
    const html = renderToStaticMarkup(<RemoveJobDialog job={job} onCancel={() => {}} onConfirm={() => {}} />);
    expect(html).toContain("Remove Acme · Sales Analyst?");
    for (const reason of REMOVAL_REASONS) expect(html).toContain(reason.label.replace("'", "&#x27;"));
    expect(html).toContain("Only this role is skipped from now on.");
  });
});

describe("Morning jobs in Settings", () => {
  const render = (schedule: Parameters<typeof MorningJobsRow>[0]["schedule"]) =>
    renderToStaticMarkup(<MorningJobsRow schedule={schedule} onChange={() => {}} />);

  it("says when the search starts and when the list is ready", () => {
    const on = render({
      enabled: true, exists: true, supported: true, ready_by: "08:00", night_start: "00:30",
      next_run: "2026-09-28T00:30:00",
    });
    expect(on).toContain("Searches overnight from 00:30");
    expect(on).toContain("ready by 08:00 (next start 2026-09-28 00:30)");
    expect(on).toContain("never submits anything");
  });

  it("is plain about off, a task that did not register, and computers without Task Scheduler", () => {
    expect(render({ enabled: false, supported: true, ready_by: "09:00" })).toContain("Off. Switch it on");
    expect(render({ enabled: true, exists: false, supported: true, note: "Access is denied." })).toContain(
      "not set up yet: Access is denied.",
    );
    const mac = render({ supported: false, note: "Scheduled tasks are set up on Windows only." });
    expect(mac).toContain("Windows only");
    expect(mac).not.toContain('aria-label="Morning jobs"');
  });
});
