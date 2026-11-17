import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { NeedsYou } from "./Dashboard";

describe("NeedsYou", () => {
  it("lists what only the person can do, with the page that resolves it and the official link", () => {
    const html = renderToStaticMarkup(
      <NeedsYou
        onGo={() => {}}
        items={[
          { id: "stamp_1g_expiry", text: "Your recorded Stamp 1G expiry is 2026-11-20, in 47 days.", where: "profile",
            url: "https://www.irishimmigration.ie/my-situation-has-changed-since-i-arrived-in-ireland/third-level-graduate-programme/" },
          { id: "pay_unconfirmed", text: "2 saved jobs state no pay.", where: "daily" },
        ]}
      />,
    );
    expect(html).toContain("Needs you");
    expect(html).toContain("in 47 days");
    expect(html).toContain("Open Profile");
    expect(html).toContain("Open Daily Search");
    expect(html).toContain("Official page");
  });

  it("shows nothing when nothing needs the person", () => {
    expect(renderToStaticMarkup(<NeedsYou items={[]} />)).toBe("");
  });
});
