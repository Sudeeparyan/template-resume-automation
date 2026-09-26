import { useState } from "react";
import { api } from "../api";
import { Badge, Field, Modal } from "./UI";
import type { Goals } from "../types";

/** Her week: planned and confirmed applications per day. It lives on the Dashboard only. */
export function WeekCard({ goals }: { goals: Goals }) {
  return (
    <section className="card week-card">
      <div className="section-title">
        <h2>Your week</h2>
        <Badge>
          {goals.week_completed}/{goals.current_week_target} this week · {goals.weekly_target} per full week
        </Badge>
      </div>
      <div className="week-grid">
        {goals.schedule.map((d) => (
          <div key={d.date} className={d.today ? "today" : ""}>
            <span>{d.label}</span>
            <small>{d.date.slice(5)}</small>
            <strong>
              {d.completed}
              <small>/{d.planned}</small>
            </strong>
            <progress max={Math.max(1, d.planned)} value={d.completed} />
          </div>
        ))}
      </div>
      <p className="small">
        Tracking starts {goals.settings.start_date}. Partial weeks are prorated. Unfinished targets carry across days
        and weeks. Saving a job or preparing a resume does not count as applying; email receipt dates are used only
        when an explicit submission date is unavailable.
      </p>
    </section>
  );
}

type Plan = Goals["settings"];

/** "Set your application plan": applications per week, start date and application days. */
export function PlanEditor({
  goals,
  onClose,
  onSaved,
  notify,
}: {
  goals: Goals;
  onClose: () => void;
  onSaved: () => Promise<void>;
  notify: (text: string, error?: boolean) => void;
}) {
  const [plan, setPlan] = useState<Plan>({ ...goals.settings });
  const [busy, setBusy] = useState(false);
  return (
    <Modal title="Set your application plan" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await api("/v2/goals", "PUT", plan);
            await onSaved();
            onClose();
            notify("Plan saved. Your carryover has been recalculated.");
          } catch (error) {
            notify((error as Error).message, true);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field label="Applications per week">
          <input
            type="number"
            min="1"
            max="200"
            required
            value={plan.weekly_target}
            onChange={(e) => setPlan({ ...plan, weekly_target: Number(e.target.value) })}
          />
        </Field>
        <Field label="Start tracking from">
          <input
            type="date"
            max={goals.date}
            required
            value={plan.start_date}
            onChange={(e) => setPlan({ ...plan, start_date: e.target.value })}
          />
        </Field>
        <fieldset>
          <legend>Application days</legend>
          <div className="day-choices">
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d, i) => (
              <label key={d}>
                <input
                  type="checkbox"
                  checked={plan.workdays.includes(i)}
                  onChange={(e) =>
                    setPlan({
                      ...plan,
                      workdays: e.target.checked ? [...plan.workdays, i] : plan.workdays.filter((n) => n !== i),
                    })
                  }
                />
                {d}
              </label>
            ))}
          </div>
        </fieldset>
        <p className="small">
          Changing this plan recalculates unfinished work from the start date. Choose a new start date if you want a
          fresh target.
        </p>
        <button className="primary" disabled={busy || !plan.workdays.length}>
          Save plan
        </button>
      </form>
    </Modal>
  );
}
