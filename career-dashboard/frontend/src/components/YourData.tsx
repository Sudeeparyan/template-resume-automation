/** Settings card: downloads of everything in this profile. */
import { Archive, Download } from "lucide-react";
import { API_BASE } from "../api";

export const backupUrl = () => `${API_BASE}/v2/export/backup.json`;
export const documentsUrl = () => `${API_BASE}/v2/export/documents.zip`;

export function YourDataCard() {
  return (
    <section className="card spaced your-data">
      <div className="section-title">
        <h2><Archive size={18} /> Your data</h2>
      </div>
      <p className="muted">
        Download this profile's records to keep or move: applications (removed ones too, with the reason), their history,
        interviews, follow-up drafts, cover letters, goals, reviewed email evidence and your profile. Keys and passwords are
        never included. The files come straight to this computer; nothing is uploaded.
      </p>
      <div className="actions">
        <a className="primary" href={backupUrl()} download><Download size={16} /> Download backup (JSON)</a>
        <a className="secondary" href={documentsUrl()} download><Download size={16} /> Download all resumes and letters (ZIP)</a>
      </div>
    </section>
  );
}
