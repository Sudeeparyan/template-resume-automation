import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { FileText, LayoutDashboard, MessageSquareText, Search, ShieldCheck, SlidersHorizontal, UserRound, Workflow } from "lucide-react";
import { PHONE_SHORT, PhoneMore } from "./PhoneNav";

const tabs = [
  { id: "assistant", label: "Assistant", Icon: MessageSquareText },
  { id: "dashboard", label: "Dashboard", Icon: LayoutDashboard },
  { id: "daily", label: "Daily Search", Icon: Search },
  { id: "resumes", label: "Resume Studio", Icon: FileText },
  { id: "assurance", label: "Assurance", Icon: ShieldCheck },
  { id: "profile", label: "Profile", Icon: UserRound },
  { id: "agents", label: "Agents", Icon: Workflow },
  { id: "settings", label: "Settings", Icon: SlidersHorizontal },
];
const none = () => {};

describe("The phone's bottom bar", () => {
  it("holds four named tabs; More is the fifth", () => {
    expect(Object.values(PHONE_SHORT)).toEqual(["Chat", "Dashboard", "Search", "Resumes"]);
  });
  it("opens a sheet with every other tab, the open one marked and running agents counted", () => {
    const html = renderToStaticMarkup(<PhoneMore open tabs={tabs} route="settings" working={2} onClose={none} onGo={none} />);
    const rows = [...html.matchAll(/<button[^>]*>.*?<\/button>/g)].map((m) => m[0]);
    expect(rows).toHaveLength(4);
    for (const label of ["Assurance", "Profile", "Agents", "Settings"]) expect(html).toContain(label);
    expect(html).not.toContain("Daily Search");
    expect(rows[3]).toContain('aria-current="page"');
    expect(rows[2]).toContain("2 working");
    expect(renderToStaticMarkup(<PhoneMore open={false} tabs={tabs} route="assistant" working={0} onClose={none} onGo={none} />)).toBe("");
  });
});
