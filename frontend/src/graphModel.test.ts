import { describe, expect, it } from "vitest";
import { buildPullRequestGraph, rankBottlenecks, type PullRequestInput } from "./graphModel";

const NOW = new Date("2026-08-18T02:00:00Z");
function pr(number: number, head: string, base = "main", overrides: Partial<PullRequestInput> = {}): PullRequestInput {
  return { number, title: `PR ${number}`, url: `https://github.com/example/pull/${number}`, author: "dev", base, head, headSha: `${number}`.repeat(40).slice(0, 40), draft: false, mergeable: true, createdAt: "2026-08-17T00:00:00Z", updatedAt: "2026-08-18T01:00:00Z", reviews: [{ user: "human", state: "APPROVED" }], checks: [{ name: "CI", status: "completed", conclusion: "success" }], ...overrides };
}
describe("PR graph model", () => {
  it("calculates stacked dependencies and transitive downstream", () => { const model = buildPullRequestGraph([pr(21,"a"),pr(22,"b","a"),pr(23,"c","b"),pr(24,"d","c")], { now: NOW }); expect(model.find((x)=>x.number===22)?.dependencies).toEqual([21]); expect(model.find((x)=>x.number===21)?.downstream).toEqual([22,23,24]); });
  it("blocks failed CI", () => expect(buildPullRequestGraph([pr(1,"a","main",{checks:[{name:"CI",status:"completed",conclusion:"failure"}]})],{now:NOW})[0].status).toBe("BLOCKED"));
  it("blocks changes requested", () => expect(buildPullRequestGraph([pr(1,"a","main",{reviews:[{user:"human",state:"CHANGES_REQUESTED"}]})],{now:NOW})[0].status).toBe("BLOCKED"));
  it("classifies stacks as waiting", () => expect(buildPullRequestGraph([pr(1,"a"),pr(2,"b","a")],{now:NOW})[1].status).toBe("WAITING"));
  it("classifies approved green main PR as ready", () => expect(buildPullRequestGraph([pr(1,"a")],{now:NOW})[0].status).toBe("READY"));
  it("classifies stale unresolved PR", () => expect(buildPullRequestGraph([pr(1,"a","main",{reviews:[],updatedAt:"2026-08-14T00:00:00Z"})],{now:NOW})[0].status).toBe("STALE"));
  it("orders downstream bottleneck first", () => expect(rankBottlenecks(buildPullRequestGraph([pr(21,"a"),pr(22,"b","a"),pr(23,"c","b"),pr(40,"x")],{now:NOW}))[0].number).toBe(21));
});

