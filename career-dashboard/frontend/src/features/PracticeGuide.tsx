import { useState } from "react";
import type { Summary } from "../types";

export function practiceSteps(data?: Summary) {
  const jobs = data?.jobs || [];
  const documents = data?.documents || [];
  const runs = data?.runs || [];
  return [
    { id: "profile", label: "Explore the synthetic profile", route: "profile", done: true },
    { id: "job", label: "Choose a fictional job", route: "daily", done: jobs.length > 0 },
    { id: "tailor", label: "Tailor and export a resume", route: "resumes", done: documents.some((item) => item.resumes.length > 0) },
    { id: "review", label: "Read the independent review", route: "assurance", done: runs.some((run) => run.kind === "resume_match" && run.state === "completed") },
    { id: "track", label: "Practice recording an application", route: "dashboard", done: jobs.some((job) => Boolean(job.application_date) || ["applied", "interview", "offer"].includes(job.status)) },
  ];
}

export default function PracticeGuide({ data, onGo }: { data?: Summary; onGo: (route: string) => void }) {
  const [open, setOpen] = useState(true);
  const steps = practiceSteps(data);
  const complete = steps.filter((step) => step.done).length;
  return (
    <section className="practice-banner" aria-label="Practice workspace">
      <button type="button" className="practice-summary" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span><strong>Practice workspace</strong><small>Simulated AI · fictional employers · no real applications</small></span>
        <span>{complete}/{steps.length} steps {open ? "−" : "+"}</span>
      </button>
      {open && <ol className="practice-steps">
        {steps.map((step) => <li key={step.id} className={step.done ? "done" : ""}>
          <button type="button" onClick={() => onGo(step.route)}>
            <span aria-hidden="true">{step.done ? "✓" : "○"}</span> {step.label}
          </button>
        </li>)}
      </ol>}
    </section>
  );
}
