import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import ApplicationFunnel from "./ApplicationFunnel";

it("shows the selected-period funnel and real next actions", () => {
  const funnel = {
    periods: {
      "30": { saved: 4, applied: 2, interviews: 1, offers: 0 },
      "90": { saved: 8, applied: 4, interviews: 2, offers: 1 },
      all: { saved: 10, applied: 5, interviews: 2, offers: 1 },
    },
    next_actions: [{ job_id: "job-1", company: "Example Employer", title: "Analyst", text: "Review the application package" }],
  };
  const html = renderToStaticMarkup(<ApplicationFunnel funnel={funnel} onJob={() => {}} />);
  expect(html).toContain("Application funnel");
  expect(html).toContain("Last 30 days");
  expect(html).toContain("Example Employer");
  expect(html).toContain("not a prediction of interview chances");
});
