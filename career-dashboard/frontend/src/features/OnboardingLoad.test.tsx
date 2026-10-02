import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import OnboardingLoad from "./OnboardingLoad";

describe("failed initial setup connection", () => {
  it("keeps the actual server error and an enabled retry instead of an endless spinner", () => {
    const html = renderToStaticMarkup(<OnboardingLoad error="The profile could not be loaded." onRetry={() => {}} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain("The profile could not be loaded.");
    expect(html).toContain("Retry setup connection");
    expect(html).not.toContain("disabled");
    expect(html).not.toContain('role="status"');
  });

  it("shows a loading state while the first read or a retry is pending", () => {
    const html = renderToStaticMarkup(<OnboardingLoad error="" onRetry={() => {}} />);
    expect(html).toContain('role="status"');
    expect(html).not.toContain('role="alert"');
  });
});
