import { useState } from "react";
import { CheckCircle2, FileText, FolderOpen, MessageSquareText, Sparkles } from "lucide-react";
import type { ProfileEntry } from "../profiles";
import { firstName } from "../profiles";
import AISetup from "./AISetup";
import Onboarding from "./Onboarding";
import OnboardingChat from "./OnboardingChat";
import SourceLibrary from "./SourceLibrary";

export default function OnboardingWorkspace({
  profile,
  notify,
  onBuilt,
  setupForm,
  chooseSetup,
}: {
  profile: ProfileEntry;
  notify: (message: string, error?: boolean) => void;
  onBuilt: () => void | Promise<void>;
  setupForm: boolean;
  chooseSetup: (form: boolean) => void;
}) {
  const [guided, setGuided] = useState(false);
  const name = firstName(profile.name) || profile.name;
  return (
    <div className="assistant-workspace setup-workspace">
      <div className="assistant-project-rail" aria-label="New profile sources">
        <SourceLibrary profile={profile} notify={notify} compact onBuilt={onBuilt} />
      </div>
      <div className="setup-center">
        <span className="setup-kicker"><Sparkles size={16} /> NEW CAREER PROJECT</span>
        <h1>Build {name}&apos;s career workspace</h1>
        <p>Add a resume, project notes, or personal information in the source library, confirm your permission to work in Build settings, then select <b>Build Agent for You</b>. The progress card shows each stage and its estimated time.</p>
        <AISetup who={name} />
        <div className="setup-steps">
          <div><FolderOpen size={19} /><b>1. Add sources</b><span>Upload Word or PDF files and write notes. Keep past versions in one place.</span></div>
          <div><Sparkles size={19} /><b>2. Build your agents</b><span>The app reads active sources and builds a profile for your chosen markets.</span></div>
          <div><CheckCircle2 size={19} /><b>3. Start working</b><span>Dashboard, Profile, job search, and Resume Studio populate from the saved profile.</span></div>
        </div>
        <button type="button" className="source-text-button setup-guide-button" onClick={() => setGuided((open) => !open)} aria-expanded={guided}>
          <MessageSquareText size={15} /> {guided ? "Hide guided review" : "Open guided review and questions"}
        </button>
        {guided && <div className="setup-guided">
          <div className="setup-guide-tabs">
            <button type="button" className={!setupForm ? "selected" : ""} onClick={() => chooseSetup(false)}>Chat review</button>
            <button type="button" className={setupForm ? "selected" : ""} onClick={() => chooseSetup(true)}>Detailed form</button>
          </div>
          {setupForm
            ? <Onboarding profile={profile} notify={notify} onUseChat={() => chooseSetup(false)} />
            : <OnboardingChat profile={profile} notify={notify} onUseForm={() => chooseSetup(true)} />}
        </div>}
      </div>
      <div className="assistant-output-rail setup-help" aria-label="Setup checklist">
        <section><h2>Your project</h2><p>Sources remain attached to this profile. You can add new versions and rebuild later from the Assistant.</p></section>
        <section><h2>Accepted sources</h2><ul className="assistant-output-list">
          <li><FileText size={15} /> Word documents (.docx)</li>
          <li><FileText size={15} /> PDF resumes and records</li>
          <li><FileText size={15} /> Text or Markdown notes</li>
        </ul></section>
        <section><h2>Review flags</h2><p>When a fact is unclear, the build shows a flag for you to check before using it in a resume.</p></section>
      </div>
    </div>
  );
}
