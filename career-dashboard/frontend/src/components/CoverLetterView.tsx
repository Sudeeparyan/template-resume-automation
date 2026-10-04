import { API_BASE, fileUrl, safeUrl } from "../api";
import { Modal } from "./UI";
import type { CoverLetter } from "../types";

/** A generated cover letter: how it was written, the company facts it cites, and its files. */
export function CoverLetterView({ letter, onClose }: { letter: CoverLetter; onClose: () => void }) {
  const how =
    letter.method === "ai"
      ? "Written by the AI from your registered evidence, then checked: every number, name and skill in it is in your evidence or the posting."
      : "Built from your own registered sentences.";
  return (
    <Modal title={`${letter.company} · Cover letter`} onClose={onClose}>
      <div className="callout warning">Draft only. Read it against the job description before sending.</div>
      <p className="muted">{how}</p>
      {letter.note && <p className="muted">{letter.note}</p>}
      {!!letter.company_facts?.length && (
        <details>
          <summary>Company facts it uses ({letter.company_facts.length})</summary>
          <ul>
            {letter.company_facts.map((fact) => (
              <li key={fact.url + fact.text}>
                {fact.text}{" "}
                <a href={safeUrl(fact.url)} target="_blank" rel="noreferrer">
                  source ↗
                </a>
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="cover-letter-preview">{letter.content}</div>
      <div className="actions">
        <button className="secondary" onClick={() => navigator.clipboard.writeText(letter.content)}>
          Copy letter
        </button>
        {letter.docx_path && (
          <a
            className="secondary"
            href={`${API_BASE}/v2/jobs/${encodeURIComponent(letter.job_id)}/cover-letter/download?format=docx`}
          >
            Download Word
          </a>
        )}
        <a className="primary" href={fileUrl(letter.path)} target="_blank" rel="noreferrer">
          Open saved file ↗
        </a>
      </div>
    </Modal>
  );
}
