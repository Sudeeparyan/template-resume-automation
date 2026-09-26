import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { WeekCard } from "./WeekPlan";
import type { Goals } from "../types";

const goals: Goals = {
  date: "2026-09-24", weekly_target: 12, current_week_target: 12, week_completed: 5,
  daily_base: 2, carryover: 1, ahead: 0, today_target: 3, today_completed: 0, remaining_today: 3, week_remaining: 7,
  schedule: [
    { date: "2026-09-21", label: "Mon", planned: 2, completed: 2, today: false },
    { date: "2026-09-24", label: "Thu", planned: 3, completed: 0, today: true },
  ],
  settings: { weekly_target: 12, workdays: [0, 1, 2, 3, 4], start_date: "2026-09-01" },
};

describe("Your week, on the Dashboard", () => {
  it("shows each planned day with today marked and the week's total", () => {
    const html = renderToStaticMarkup(<WeekCard goals={goals} />);
    expect(html).toContain("Your week");
    expect(html).toContain("5/12 this week · 12 per full week");
    expect(html).toContain('<div class="today"><span>Thu</span><small>09-24</small>');
    expect(html).toContain("Tracking starts 2026-09-01");
    expect(html).toContain("does not count as applying");
  });
});
