/** The profile strength meter: how ready the profile is to make good resumes, and the next
 * three things to add. It reads only the evidence the resumes use (services/profile_strength.py). */
import { CheckCircle2, Circle, Plus } from "lucide-react";
import type { ProfileStrength as Strength } from "../types";

/** The Profile form each missing piece is added with. */
const ADD_KIND: Record<string, string> = {
  contact: "personal",
  work_authorization: "personal",
  target_roles: "personal",
  education: "education",
  experience: "experience",
  skills: "skill",
  certification: "certification",
};

export function strengthWord(score: number): string {
  return score >= 90 ? "Ready for strong resumes" : score >= 70 ? "Nearly there" : score >= 40 ? "Taking shape" : "Just started";
}

export default function ProfileStrength({ strength, onAdd }: { strength: Strength; onAdd?: (kind: string) => void }) {
  const { score, checks, next, tips } = strength;
  return (
    <section className="card profile-strength" aria-label="Profile strength">
      <div className="strength-head">
        <div className="strength-dial" style={{ ["--pct" as string]: `${score}%` }} role="img" aria-label={`Profile strength ${score} out of 100`}>
          <b>{score}</b>
          <small>/100</small>
        </div>
        <div>
          <div className="eyebrow">PROFILE STRENGTH</div>
          <h2>{strengthWord(score)}</h2>
          <p className="small muted">Counts only what a tailored CV needs. Nothing here is a judgement of you.</p>
        </div>
      </div>
      <ul className="strength-checks">
        {checks.map((check) => (
          <li key={check.id} className={check.done ? "done" : ""}>
            {check.done ? <CheckCircle2 size={16} aria-hidden="true" /> : <Circle size={16} aria-hidden="true" />}
            <span>{check.label}</span>
            <span className="visually-hidden">{check.done ? "done" : "to do"}</span>
          </li>
        ))}
      </ul>
      {(next.length > 0 || tips.length > 0) && (
        <div className="strength-next">
          <h3>Add next</h3>
          <ol>
            {[...next, ...(next.length < 3 ? tips : [])].map((item) => (
              <li key={item.id}>
                <span>
                  <b>{item.label}</b>
                  <small>{item.why}</small>
                </span>
                {onAdd && ADD_KIND[item.id] && (
                  <button type="button" className="secondary" onClick={() => onAdd(ADD_KIND[item.id])}>
                    <Plus size={15} /> Add
                  </button>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
