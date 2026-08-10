import { expect, test } from "@playwright/test";

const snapshot = (repo: string) => ({
  repository: repo,
  fetchedAt: "2026-08-18T02:00:00Z",
  rateLimit: { remaining: 4990, limit: 5000 }, authentication: "authenticated", cache: { hit: false, ttlSeconds: 45 },
  pulls: [
    { number: 21, title: "Base architecture", state: "open", url: "https://github.com/x/pull/21", author: "dev", base: "main", head: "stack-a", headSha: "a".repeat(40), draft: false, mergeable: true, mergeableState: "clean", createdAt: "2026-08-17T00:00:00Z", updatedAt: "2026-08-18T01:00:00Z", requestedReviewers: [], labels: [], commitCount: 1, reviews: [{ user: "human", state: "APPROVED" }], checks: [{ name: "CI", status: "completed", conclusion: "success" }] },
    { number: 22, title: "Dependent UI", state: "open", url: "https://github.com/x/pull/22", author: "dev2", base: "stack-a", head: "stack-b", headSha: "b".repeat(40), draft: false, mergeable: true, mergeableState: "clean", createdAt: "2026-08-17T00:00:00Z", updatedAt: "2026-08-18T01:00:00Z", requestedReviewers: ["reviewer"], labels: ["ui"], commitCount: 1, reviews: [], checks: [{ name: "CI", status: "completed", conclusion: "success" }] },
  ],
  commits: [{ sha: "a".repeat(40), message: "base commit", author: "Dev", timestamp: "2026-08-18T01:00:00Z", parents: [], branch: "stack-a", prNumber: 21 }],
});

test.beforeEach(async ({ page }) => {
  await page.route("**/api/repositories", (route) => route.fulfill({ json: { repositories: ["Biz-CollabCraft/ontology_dashboard", "Biz-CollabCraft/gen_data"] } }));
  await page.route("**/api/snapshot?**", (route) => { const url = new URL(route.request().url()); route.fulfill({ json: snapshot(url.searchParams.get("repo") ?? "unknown") }); });
});

test("root renders PR graph, dependency, drawer, commit graph and repository selector", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("dashboard-root")).toBeVisible();
  await expect(page.getByText("Open PRs").locator("..").getByText("2")).toBeVisible();
  await expect(page.getByTestId("pr-flow-graph")).toHaveAttribute("data-edge-count", "1");
  await page.locator(".react-flow__node").filter({ hasText: "#22" }).click();
  await expect(page.getByRole("dialog", { name: "PR #22 details" })).toContainText("Depends on");
  await page.getByRole("button", { name: "×" }).click();
  await page.getByRole("button", { name: "Commit Graph" }).click();
  await expect(page.getByTestId("commit-graph")).toBeVisible();
  await page.getByLabel("Repository").selectOption("Biz-CollabCraft/gen_data");
  await expect(page.getByLabel("Repository")).toHaveValue("Biz-CollabCraft/gen_data");
});

test("shows API fallback error", async ({ page }) => {
  await page.unroute("**/api/snapshot?**");
  await page.route("**/api/snapshot?**", (route) => route.fulfill({ status: 502, json: { detail: "GitHub API unavailable" } }));
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("GitHub API unavailable");
});

