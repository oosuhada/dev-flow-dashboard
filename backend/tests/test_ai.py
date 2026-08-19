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

    async def fake_generate(_system: str, prompt: str, max_output_tokens: int = 4096, **_kwargs):
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

    async def fake_generate(_system: str, _prompt: str, max_output_tokens: int = 4096, **_kwargs):
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


@pytest.mark.asyncio
async def test_project_pm_human_changes_requested_forces_author_fix_before_merge(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_FLOW_AI_ENABLED", "true")
    monkeypatch.setenv("DEV_FLOW_AI_API_KEY", "test-key")
    monkeypatch.setenv("DEV_FLOW_AI_STATE_DIR", str(tmp_path))
    advisor = VertexAIAdvisor()
    current = snapshot()
    pull_23 = next(item for item in current["pulls"] if item["number"] == 23)
    pull_23["reviews"] = [
        {"user": "reviewer1", "state": "CHANGES_REQUESTED", "body": "fix shutdown", "isBot": False},
        {"user": "github-actions[bot]", "state": "COMMENTED", "body": "ready", "isBot": True},
    ]
    prompts: list[dict] = []

    async def fake_generate(_system: str, prompt: str, max_output_tokens: int = 4096, **_kwargs):
        prompts.append(json.loads(prompt))
        return ({
            "headline": "merge #23 now",
            "projectHealth": "ON_TRACK",
            "healthReason": "tests green",
            "currentStep": {"number": 4, "name": "runtime", "confidence": .9, "why": "x", "exitGate": "y"},
            "currentObjective": "ship",
            "antiPatternAlerts": [],
            "teamActions": [
                {"name": "Alice", "github": "alice", "role": "r1", "now": "other", "whyNow": "x", "nextHandoff": "", "blockedBy": [], "relatedWork": [], "confidence": .8},
                {"name": "Bob", "github": "bob", "role": "r2", "now": "merge #23", "whyNow": "green", "nextHandoff": "", "blockedBy": [], "relatedWork": [], "confidence": .8},
                {"name": "Carol", "github": "carol", "role": "r3", "now": "other", "whyNow": "x", "nextHandoff": "", "blockedBy": [], "relatedWork": [], "confidence": .8},
                {"name": "Dave", "github": "dave", "role": "r4", "now": "other", "whyNow": "x", "nextHandoff": "", "blockedBy": [], "relatedWork": [], "confidence": .8},
            ],
            "prPriorities": [
                {"repository": "A/one", "number": 23, "rank": 1, "priority": "P0", "reason": "green", "nextAction": "reviewer merge", "actorGithub": "reviewer1", "impact": "ship", "confidence": .9},
            ],
            "changesSinceLast": [],
            "contextSummary": "ctx",
        }, {"totalTokenCount": 100})

    monkeypatch.setattr(advisor, "_generate_json", fake_generate)
    project_memory = {
        "roles": [
            {"name": "Alice", "github": "alice", "role": "r1"},
            {"name": "Bob", "github": "bob", "role": "r2"},
            {"name": "Carol", "github": "carol", "role": "r3"},
            {"name": "Dave", "github": "dave", "role": "r4"},
        ],
        "steps": [],
    }
    result = await advisor.analyze_project(
        {"A/one": current},
        {"repository": "A/one", "event": "pull_request_review", "action": "submitted", "number": 23, "eventContext": {"reviewState": "changes_requested", "reviewBody": "fix shutdown"}},
        project_memory,
    )

    priority = result["prPriorities"][0]
    bob = next(item for item in result["teamActions"] if item["github"] == "bob")
    assert priority["actorGithub"] == "bob"
    assert "CHANGES_REQUESTED" in priority["nextAction"]
    assert "merge 금지" in priority["nextAction"]
    assert "PR #23" in bob["now"]
    assert "재검증" in bob["now"]
    assert prompts[0]["trigger"]["eventContext"]["reviewBody"] == "fix shutdown"
    assert prompts[0]["unresolvedHumanChangesRequested"][0]["number"] == 23


def test_project_semantic_revision_ignores_volatile_updated_at(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_FLOW_AI_STATE_DIR", str(tmp_path))
    advisor = VertexAIAdvisor()
    first = snapshot()
    second = snapshot()
    second["pulls"][0]["updatedAt"] = "2026-08-19T12:34:56Z"

    revision_a = advisor.project_semantic_revision({"A/one": first}, {"revision": "docs-v1"})
    revision_b = advisor.project_semantic_revision({"A/one": second}, {"revision": "docs-v1"})
    assert revision_a == revision_b

    # Raw push/default-head churn and automated review prose are not reasons
    # for fallback polling to spend another PM call.
    second["headSha"] = "def"
    second["commits"] = [
        {"sha": "def", "message": "bot-only push", "author": "bot", "timestamp": "now"}
    ]
    second["pulls"][0]["reviews"] = [
        {"user": "github-actions[bot]", "state": "COMMENTED", "body": "automated", "isBot": True}
    ]
    revision_noise = advisor.project_semantic_revision({"A/one": second}, {"revision": "docs-v1"})
    assert revision_noise == revision_a

    second["pulls"][0]["mergeableState"] = "blocked"
    revision_c = advisor.project_semantic_revision({"A/one": second}, {"revision": "docs-v1"})
    assert revision_c != revision_a


def test_auto_pm_budget_enforces_daily_calls_and_input_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_FLOW_AI_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DEV_FLOW_AI_DAILY_CALL_LIMIT", "2")
    monkeypatch.setenv("DEV_FLOW_AI_DAILY_INPUT_TOKEN_LIMIT", "10000")
    advisor = VertexAIAdvisor()

    assert advisor.auto_pm_allowed() is True
    advisor.record_auto_pm_usage({"promptTokenCount": 4000})
    assert advisor.auto_pm_allowed() is True
    status = advisor.record_auto_pm_usage({"promptTokenCount": 4000})
    assert status["calls"] == 2
    assert status["inputTokens"] == 8000
    assert status["automaticAllowed"] is False


def test_project_model_tier_uses_reasoning_for_human_review(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_FLOW_AI_STATE_DIR", str(tmp_path))
    advisor = VertexAIAdvisor()

    simple, simple_thinking = advisor.project_model_for(
        {"event": "pull_request", "action": "opened"}
    )
    reasoning, reasoning_thinking = advisor.project_model_for(
        {
            "event": "pull_request_review",
            "eventContext": {"reviewState": "changes_requested"},
        }
    )
    assert simple == "gemini-3.5-flash-lite"
    assert simple_thinking is None
    assert reasoning == "gemini-3.7-flash"
    assert reasoning_thinking == "MEDIUM"
