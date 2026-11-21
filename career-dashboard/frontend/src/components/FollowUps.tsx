/** Follow-ups and thank-you notes that are due. The app only drafts: the person copies the
 * note, sends it from their own email, then may record that they sent it. A due follow-up
 * never changes an application's status. */
import { useState } from "react";
import { Check, Copy, MailQuestion, PenLine } from "lucide-react";
import { api } from "../api";
import { Badge, Modal } from "./UI";
import type { Notify } from "./UI";
import type { FollowUpDraft, FollowUpItem } from "../types";

export function FollowUpsCard({ items, notify, refresh, onOpen }: {
  items: FollowUpItem[];
  notify: Notify;
  refresh: () => Promise<void>;
  onOpen: (jobId: string) => void;
}) {
  const [open, setOpen] = useState<{ item: FollowUpItem; draft: FollowUpDraft } | null>(null);
  const [busy, setBusy] = useState("");
  async function draft(item: FollowUpItem, fresh = false) {
    if (item.draft && !fresh) {
      setOpen({ item, draft: item.draft });
      return;
    }
    setBusy(item.job_id + item.kind);
    try {
      const made = await api<FollowUpDraft>(`/v2/jobs/${encodeURIComponent(item.job_id)}/follow-up`, "POST", { kind: item.kind });
      setOpen({ item, draft: made });
      await refresh();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="card follow-ups" aria-label="Follow-ups due">
      <div className="section-title">
        <h2><MailQuestion size={19} /> Follow up</h2>
        {!!items.length && <Badge tone="amber">{items.length} due</Badge>}
      </div>
      {items.length ? (
        <ul className="follow-up-list">
          {items.slice(0, 5).map((item) => (
            <li key={item.job_id + item.kind}>
              <button type="button" className="link-button follow-up-open" onClick={() => onOpen(item.job_id)}>
                <b>{item.company}</b>
                <span>{item.title}</span>
                <small>{item.reason}</small>
              </button>
              <button type="button" className="secondary" disabled={busy === item.job_id + item.kind} onClick={() => void draft(item)}>
                <PenLine size={15} /> {item.draft ? "Open draft" : item.kind === "thank_you" ? "Draft thank-you" : "Draft follow-up"}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="small muted">Nothing to chase. Applications quiet for 10 days, and interviews you just had, show up here with a draft note.</p>
      )}
      {open && (
        <DraftModal item={open.item} draft={open.draft} notify={notify} onClose={() => setOpen(null)}
          onRedraft={() => void draft(open.item, true)}
          onSent={async () => {
            try {
              await api(`/v2/follow-ups/${open.draft.id}/sent`, "POST");
              setOpen(null);
              await refresh();
              notify(`Recorded that you sent it to ${open.item.company}. Your application status is unchanged.`);
            } catch (e) {
              notify((e as Error).message, true);
            }
          }} />
      )}
    </section>
  );
}

function DraftModal({ item, draft, notify, onClose, onRedraft, onSent }: {
  item: FollowUpItem;
  draft: FollowUpDraft;
  notify: Notify;
  onClose: () => void;
  onRedraft: () => void;
  onSent: () => void;
}) {
  const [text, setText] = useState(draft.text);
  const [copied, setCopied] = useState(false);
  return (
    <Modal title={`${item.kind === "thank_you" ? "Thank-you note" : "Follow-up"} for ${item.company}`} onClose={onClose}>
      <p className="small">
        A draft for you to check, edit and send from your own email. The app never sends it. It uses only the role, the
        dates you recorded and your name.
      </p>
      <textarea className="follow-up-text" rows={12} value={text} onChange={(e) => setText(e.target.value)} aria-label="Draft note" />
      <div className="actions">
        <button type="button" className="primary" onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
          } catch {
            notify("Copying is blocked here. Select the text and copy it yourself.", true);
          }
        }}>
          {copied ? <Check size={16} /> : <Copy size={16} />} {copied ? "Copied" : "Copy note"}
        </button>
        <button type="button" className="secondary" onClick={onSent}>I sent it</button>
        <button type="button" className="text-button" onClick={onRedraft}>Start the draft again</button>
      </div>
    </Modal>
  );
}
