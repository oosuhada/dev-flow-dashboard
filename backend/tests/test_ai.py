from __future__ import annotations

import json

import pytest

from backend.app.ai import VertexAIAdvisor


def snapshot() -> dict:
    return {
        "repository": "A/one",
        "defaultBranch": "main",
        "headSha": "abc",
        "pullRelations": [{"source": 22, "target": 23, "kind": "stacked", "reason": "test"}],
        "pulls": [
            {
                "number": 22,
                "title": "upstream",
                "author": "alice",
                "head": "feature/a",
                "base": "main",
                "lifecycle": "open",
                "draft": False,
                "mergeable": True,
                "mergeableState": "clean",
                "updatedAt": "2026-08-18T00:00:00Z",
                "requestedReviewers": [],
                "labels": [],
                "commitCount": 2,
                "reviews": [],
                "checks": [],
            },
            {
                "number": 23,
                "title": "downstream",
                "author": "bob",
                "head": "feature/b",
                "base": "feature/a",
                "lifecycle": "open",
                "draft": False,
                "mergeable": True,
                "mergeableState": "clean",
                "updatedAt": "2026-08-18T00:01:00Z",
                "requestedReviewers": [],
                "labels": [],
                "commitCount": 1,
                "reviews": [],
                "checks": [],
            },
            {
                "number": 20,
                "title": "already merged",
                "author": "carol",
                "head": "old",
                "base": "main",
                "lifecycle": "merged",
                "draft": False,
                "reviews": [],
                "checks": [],
            },
        ],
        "commits": [],
    }


@pytest.mark.asyncio
async def test_repository_analysis_keeps_rolling_context_and_filters_hallucinated_prs(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_FLOW_AI_ENABLED", "true")
    monkeypatch.setenv("DEV_FLOW_AI_API_KEY", "test-key")
    monkeypatch.setenv("DEV_FLOW_AI_STATE_DIR", str(tmp_path))
    advisor = VertexAIAdvisor()
    prompts: list[dict] = []

    async def fake_generate(_system: str, prompt: str, max_output_tokens: int = 4096):
        prompts.append(json.loads(prompt))
        return ({
            "headline": "do upstream first",
            "summary": "flow summary",
            "changesSinceLast": ["changed"],
            "priorities": [
                {"rank": 1, "number": 22, "priority": "P0", "score": 90, "reason": "blocks", "nextAction": "review", "actor": "reviewer", "impact": "unblocks #23", "confidence": .9},
                {"rank": 2, "number": 999, "priority": "P1", "score": 80, "reason": "invented", "nextAction": "no", "actor": "team", "impact": "none", "confidence": .1},
            ],
            "watch": [{"number": 999, "signal": "invented"}],
            "contextSummary": f"context-{len(prompts)}",
        }, {"totalTokenCount": 123})

    monkeypatch.setattr(advisor, "_generate_json", fake_generate)
    first = await advisor.analyze_repository("A/one", snapshot(), {"event": "pull_request", "action": "opened", "number": 22})
    second = await advisor.analyze_repository("A/one", snapshot(), {"event": "issue_comment", "action": "created", "number": 22})

    assert [item["number"] for item in first["priorities"]] == [22]
    assert first["watch"] == []
    assert second["analysisSequence"] == 2
    assert len(second["recentTriggers"]) == 2
    assert prompts[1]["previousContext"] == "context-1"
    assert prompts[1]["recentTriggers"][0]["event"] == "pull_request"


@pytest.mark.asyncio
async def test_commit_analysis_filters_related_prs_and_caches_within_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_FLOW_AI_ENABLED", "true")
    monkeypatch.setenv("DEV_FLOW_AI_API_KEY", "test-key")
    monkeypatch.setenv("DEV_FLOW_AI_STATE_DIR", str(tmp_path))
    advisor = VertexAIAdvisor()
    advisor.store.save("A/one", {"generatedAt": "v1", "contextSummary": "repo context"})
    calls = 0

    async def fake_generate(_system: str, _prompt: str, max_output_tokens: int = 4096):
        nonlocal calls
        calls += 1
        return ({
            "summary": "impact",
            "riskLevel": "MEDIUM",
            "whyItMatters": "why",
            "affectedAreas": ["backend"],
            "reviewFocus": ["contract"],
            "relatedPulls": [22, 999],
            "recommendedNextStep": "review #22",
            "confidence": .8,
        }, {"totalTokenCount": 50})

    monkeypatch.setattr(advisor, "_generate_json", fake_generate)
    commit = {"sha": "abc123", "message": "change", "author": "alice", "stats": {}, "parents": [], "files": []}
    first = await advisor.analyze_commit("A/one", commit, snapshot())
    second = await advisor.analyze_commit("A/one", commit, snapshot())

    assert first["relatedPulls"] == [22]
    assert second == first
    assert calls == 1
