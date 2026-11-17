import { LoaderCircle } from "lucide-react";

/** An initial setup request failure must leave a way to recover in the same profile. */
export default function OnboardingLoad({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div className="onboarding">
      {error ? (
        <div className="callout warning" role="alert">
          <p>{error}</p>
          <button className="secondary" onClick={onRetry}>Retry setup connection</button>
        </div>
      ) : (
        <div role="status"><LoaderCircle className="spin" /> Loading…</div>
      )}
    </div>
  );
}
