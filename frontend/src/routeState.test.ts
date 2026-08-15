import { describe, expect, it } from "vitest";

import { buildDashboardPath, parseDashboardRoute } from "./routeState";

describe("dashboard route state", () => {
  it("maps the three primary dashboard views", () => {
    expect(parseDashboardRoute("/dev_dashboard/graph").tab).toBe("commits");
    expect(parseDashboardRoute("/dev_dashboard/PR").tab).toBe("pulls");
    expect(parseDashboardRoute("/dev_dashboard/board").tab).toBe("status");
  });

  it("round-trips AI panel and PR inspector state", () => {
    const route = parseDashboardRoute("/dev_dashboard/PR/chat/pull/86/checks");
    expect(route).toMatchObject({ tab: "pulls", aiTab: "chat", pullNumber: 86, pullTab: "checks" });
    expect(buildDashboardPath(route)).toBe("/dev_dashboard/PR/chat/pull/86/checks");
  });

  it("round-trips commit inspector mode", () => {
    const route = parseDashboardRoute("/dev_dashboard/graph/activity/commit/abc123/file");
    expect(route).toMatchObject({ tab: "commits", aiTab: "activity", commitSha: "abc123", commitMode: "file" });
    expect(buildDashboardPath(route)).toBe("/dev_dashboard/graph/activity/commit/abc123/file");
  });
});

