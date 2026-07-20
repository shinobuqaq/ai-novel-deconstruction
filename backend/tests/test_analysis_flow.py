from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models import AnalysisRunTask, EntityCandidate, EventCandidate, NarrativeSynthesis, Task, TaskAttempt, TaskStatus
from app.providers.base import ProviderError, ProviderResponse
from app.providers.openai_responses import OpenAIResponsesProvider
from app.providers.registry import ProviderRegistry
from app.repositories import claim_next_task
from app.services.analysis import parse_provider_output, persist_analysis_output
from app.services.provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    save_analysis_profile,
    save_model_service,
)
from app.services.tasks import execute_task_sync


ANALYSIS_OUTPUT = {
    "entities": [
        {
            "name": "林舟",
            "entity_type": "PERSON",
            "aliases": [],
            "description": "在雨夜回到旧宅的人。",
            "evidence_quotes": ["林舟推开旧宅的木门"],
            "confidence": 96,
        },
        {
            "name": "不存在的人",
            "entity_type": "PERSON",
            "aliases": [],
            "description": "这条候选没有原文依据。",
            "evidence_quotes": ["原文里没有这句话"],
            "confidence": 10,
        },
    ],
    "events": [
        {
            "title": "林舟发现密信",
            "event_type": "DISCOVERY",
            "summary": "林舟在桌上发现一封写着自己名字的密信。",
            "participants": ["林舟"],
            "narrative_mode": "ACTUAL",
            "location": "旧宅",
            "trigger": "林舟回到旧宅并进入房间。",
            "process": "林舟看见桌上放着一封写有自己名字的密信。",
            "outcome": "林舟确认有人专门给自己留下了密信。",
            "impact": "林舟决定追查寄信人的身份和目的。",
            "evidence_quotes": ["桌上放着一封写着他名字的密信"],
            "confidence": 94,
        }
    ],
}


class StaticAnalysisProvider:
    name = "openai"

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        if task_kind == "analysis.entities_events":
            assert "林舟推开旧宅的木门" in payload["input"]
            output = ANALYSIS_OUTPUT
        elif task_kind == "analysis.narrative_synthesis":
            foundation = json.loads(payload["input"])
            character = foundation["characters"][0]
            event = foundation["events"][0]
            evidence_id = event["evidence_ids"][0]
            output = {
                "story_overview": {
                    "premise": "林舟在雨夜回到旧宅，意外发现一封写给自己的密信。",
                    "synopsis": "林舟回到旧宅后发现神秘密信，并决定在天亮后寻找寄信人。",
                    "protagonist": character["name"],
                    "protagonist_goal": "找到密信的寄信人并弄清来意。",
                    "central_conflict": "密信来源不明，林舟掌握的信息不足。",
                    "opening_situation": "林舟在雨夜独自回到旧宅，原本只准备暂时安顿。",
                    "development_path": [
                        "林舟进入旧宅并发现写着自己名字的密信。",
                        "密信来源不明，使平静的归来变成需要追查的谜团。",
                        "林舟决定天亮后主动寻找寄信人。",
                    ],
                    "turning_points": ["密信出现改变了林舟回到旧宅后的行动目标。"],
                    "current_situation": "林舟已经决定主动追查，但尚未找到寄信人。",
                    "current_result": "林舟掌握了密信这一线索，并从被动发现转为主动追查。",
                    "unresolved_questions": ["寄信人是谁？", "密信为何写给林舟？"],
                    "evidence_ids": [evidence_id],
                },
                "character_roles": [
                    {
                        "name": character["name"],
                        "role": "PROTAGONIST",
                        "role_reason": "所有已识别行动和决定都围绕林舟展开。",
                        "identities": ["旧宅归来者"],
                        "goals": ["找到寄信人"],
                        "motivations": ["弄清密信来意"],
                        "abilities": [],
                        "secrets": ["暂未揭示"],
                        "important_experiences": ["雨夜发现密信"],
                        "current_state": "已发现密信并决定追查。",
                        "arc_summary": "从被动发现转向主动追查。",
                        "evidence_ids": [evidence_id],
                    }
                ],
                "character_relations": [],
                "narrative_phases": [
                    {
                        "title": "雨夜归来与密信出现",
                        "situation": "林舟在雨夜回到旧宅，平静的归来被密信打破。",
                        "goal": "弄清密信从何而来。",
                        "obstacle": "寄信人没有现身，线索有限。",
                        "key_actions": ["林舟进入旧宅", "林舟发现密信"],
                        "outcome": "林舟决定天亮后寻找寄信人。",
                        "change": "林舟从被动发现转为主动追查。",
                        "next_hook": "寄信人的身份和目的仍然未知。",
                        "event_ids": [event["id"]],
                        "evidence_ids": [evidence_id],
                    }
                ],
                "event_relations": [],
            }
        else:
            assert task_kind == "analysis.deep_insights"
            foundation = json.loads(payload["input"])
            character = foundation["characters"][0]
            event = foundation["events"][0]
            evidence_id = foundation["evidence"][0]["id"]
            is_revision = bool(foundation.get("revision_requests"))
            output = {
                "fact_versions": [
                    {
                        "subject": character["name"],
                        "predicate": "当前目标",
                        "value": "找到密信的寄信人并核对其来意" if is_revision else "找到密信的寄信人",
                        "fact_type": "STATUS",
                        "status": "CONFIRMED",
                        "valid_from_chapter": 2,
                        "valid_to_chapter": None,
                        "evidence_ids": [evidence_id],
                        "counter_evidence_ids": [],
                    }
                ],
                "state_changes": [
                    {
                        "subject": character["name"],
                        "aspect": "行动方向",
                        "before": "被动回到旧宅",
                        "after": "决定主动寻找寄信人",
                        "chapter_ordinal": 2,
                        "event_id": event["id"],
                        "evidence_ids": [evidence_id],
                    }
                ],
                "actor_knowledge": [
                    {
                        "actor": character["name"],
                        "proposition": "知道桌上有一封写着自己名字的密信",
                        "state": "KNOWS",
                        "chapter_ordinal": 1,
                        "evidence_ids": [evidence_id],
                    }
                ],
                "world_rules": [
                    {
                        "title": "密信留下追查线索",
                        "description": "写明收信人的密信可以成为追查寄信人的直接线索。",
                        "limitations": ["寄信人身份仍需核实"],
                        "costs": [],
                        "exceptions": [],
                        "evidence_ids": [evidence_id],
                    }
                ],
                "foreshadowing": [
                    {
                        "title": "密信来源",
                        "setup": "密信的寄信人和目的尚未揭示。",
                        "lifecycle": "OPEN",
                        "setup_chapter": 1,
                        "payoff_chapter": None,
                        "event_ids": [event["id"]],
                        "evidence_ids": [evidence_id],
                    }
                ],
                "conflicts": [
                    {
                        "title": "寻找密信寄信人",
                        "conflict_type": "PERSON_V_WORLD",
                        "participants": [character["name"]],
                        "goals": "找到寄信人并弄清来意。",
                        "obstacles": "寄信人没有现身。",
                        "stakes": "林舟无法判断密信是否带来危险。",
                        "escalation": [],
                        "resolution": "模型试图改写未受影响的冲突。" if is_revision else "尚未解决。",
                        "status": "OPEN",
                        "event_ids": [event["id"]],
                        "evidence_ids": [evidence_id],
                    }
                ],
                "scene_analysis": [
                    {
                        "chapter_ordinal": 1,
                        "function": "REVELATION",
                        "summary": "归来场景通过密信释放新的追查线索。",
                        "information_released": ["存在写给林舟的密信"],
                        "action_dialogue_balance": "BALANCED",
                        "pace": "STEADY",
                        "evidence_ids": [evidence_id],
                    }
                ],
                "claims": [
                    {
                        "claim_kind": "INFERENCE",
                        "claim_text": "密信促使林舟主动追查寄信人。" if is_revision else "密信把林舟从回到旧宅的被动局面推向主动追查。",
                        "scope": "前两章",
                        "evidence_ids": [evidence_id],
                        "counter_evidence_ids": [],
                        "confidence": 88,
                    }
                ],
                "entity_resolutions": [],
            }
        return ProviderResponse(
            raw_text=json.dumps(output, ensure_ascii=False),
            parsed=output,
            prompt_tokens=120,
            completion_tokens=80,
            parameters={
                "cost": {
                    "currency": "CNY",
                    "input_price_per_million_tokens": 10.0,
                    "output_price_per_million_tokens": 20.0,
                    "prompt_tokens": 120,
                    "completion_tokens": 80,
                    "input_cost": 0.0012,
                    "output_cost": 0.0016,
                    "total_cost": 0.0028,
                }
            },
        )


class AuthenticationFailureProvider:
    name = "openai"

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        raise ProviderError(
            code="PROVIDER_AUTH_FAILED",
            message="API Key 无效或没有使用该模型的权限。",
            retryable=False,
        )


class InvalidStructureProvider:
    name = "openai"

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        output = {
            "entities": [
                {
                    "name": "林舟",
                    "entity_type": "PERSON",
                    "aliases": [],
                    "description": "雨夜回到旧宅的人。",
                    "evidence_quotes": ["林舟推开旧宅的木门"],
                }
            ],
            "events": [],
        }
        return ProviderResponse(
            raw_text=json.dumps(output, ensure_ascii=False),
            parsed=output,
            prompt_tokens=42,
            completion_tokens=17,
            provider_id="compatible-test",
            model="test-model",
        )


def _import_confirmed_novel(client) -> dict:
    project = client.post("/api/projects", json={"name": "雨夜旧宅"}).json()
    source = (
        "第一章 归来\n"
        "雨下得很大，林舟推开旧宅的木门。\n"
        "桌上放着一封写着他名字的密信。\n"
        "第二章 决定\n"
        "林舟决定天亮后去找寄信人。"
    )
    imported = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=rain.txt",
        content=source.encode("utf-8"),
    )
    assert imported.status_code == 201
    result = imported.json()
    assert not [item for item in result["issues"] if item["severity"] == "BLOCKING"]
    confirmed = client.post(f"/api/source-versions/{result['version']['id']}/confirm")
    assert confirmed.status_code == 200
    return result


def test_analysis_requires_local_provider_configuration(client) -> None:
    imported = _import_confirmed_novel(client)

    response = client.post(
        f"/api/source-versions/{imported['version']['id']}/analysis/entities-events/start"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"


def test_analysis_estimate_uses_local_batches_and_saved_pricing(client) -> None:
    imported = _import_confirmed_novel(client)
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="费用估算服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://provider.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="人物与事件精确提取",
        service_id=service.id,
        model="priced-model",
        temperature=None,
        max_output_tokens=4_000,
        reasoning_effort="auto",
        timeout_seconds=60,
        max_retries=2,
        context_window_tokens=32_000,
        input_price_per_million_tokens=2.0,
        output_price_per_million_tokens=8.0,
        price_currency="USD",
    )

    response = client.get(
        f"/api/source-versions/{imported['version']['id']}/analysis/entities-events/estimate"
    )

    assert response.status_code == 200
    estimate = response.json()
    assert estimate["batch_count"] == 1
    assert estimate["planned_call_count"] == 3
    assert estimate["retry_ceiling_call_count"] == 9
    assert estimate["pricing_available"] is True
    assert estimate["cost_currency"] == "USD"
    assert estimate["maximum_cost_without_retries"] > 0
    assert estimate["maximum_cost_with_retries"] == pytest.approx(
        estimate["maximum_cost_without_retries"] * 3
    )


def test_provider_config_never_returns_plain_api_key(client) -> None:
    saved = client.put(
        "/api/settings/openai",
        json={"api_key": "sk-test-secret-value"},
    )

    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert "api_key" not in saved.json()
    loaded = client.get("/api/settings/openai")
    assert "api_key" not in loaded.json()
    config_path = client.app.state.settings.workspace_dir / "secrets" / "openai.json"
    assert json.loads(config_path.read_text(encoding="utf-8"))["api_key"] == "sk-test-secret-value"


def test_entities_events_flow_keeps_exact_source_evidence_and_is_idempotent(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-test"})
    version_id = imported["version"]["id"]

    started = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    )
    assert started.status_code == 201
    run = started.json()
    assert run["status"] == "PENDING"
    assert run["total_batches"] == 1

    registry = ProviderRegistry([StaticAnalysisProvider()])
    with client.app.state.session_factory() as session:
        claim = claim_next_task(session, worker_id="analysis-test-worker", lease_seconds=60)
    assert claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        claim,
        registry,
    )

    progress = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    ).json()
    assert progress["status"] == "PENDING"
    assert progress["completed_batches"] == 1
    assert progress["failed_batches"] == 0
    assert progress["total_batches"] == 2

    with client.app.state.session_factory() as session:
        narrative_claim = claim_next_task(
            session,
            worker_id="narrative-test-worker",
            lease_seconds=60,
        )
    assert narrative_claim is not None
    assert narrative_claim.kind == "analysis.narrative_synthesis"
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        narrative_claim,
        registry,
    )

    progress = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    ).json()
    assert progress["status"] == "REVIEW"
    assert progress["completed_batches"] == 2
    assert progress["failed_batches"] == 0
    assert progress["total_batches"] == 2

    foundation_workbench = client.get(
        f"/api/analysis-runs/{run['id']}/workbench"
    ).json()
    assert foundation_workbench["narrative_status"] == "READY"
    assert foundation_workbench["deep_status"] == "NOT_GENERATED"

    continue_analysis = client.post(
        f"/api/analysis-runs/{run['id']}/deep/start"
    )
    assert continue_analysis.status_code == 202
    assert continue_analysis.json()["status"] == "PENDING"
    assert continue_analysis.json()["total_batches"] == 3

    with client.app.state.session_factory() as session:
        deep_claim = claim_next_task(
            session,
            worker_id="deep-analysis-test-worker",
            lease_seconds=60,
        )
    assert deep_claim is not None
    assert deep_claim.kind == "analysis.deep_insights"
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        deep_claim,
        registry,
    )

    progress = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    ).json()
    assert progress["status"] == "REVIEW"
    assert progress["completed_batches"] == 3
    assert progress["failed_batches"] == 0

    diagnostics = client.get(
        f"/api/analysis-runs/{run['id']}/diagnostics"
    ).json()
    assert diagnostics["attempt_count"] == 3
    assert diagnostics["retry_count"] == 0
    assert diagnostics["prompt_tokens"] == 360
    assert diagnostics["completion_tokens"] == 240
    assert diagnostics["actual_cost"] == pytest.approx(0.0084)
    assert diagnostics["cost_currency"] == "CNY"
    assert diagnostics["cost_complete"] is True
    assert all(item["actual_cost"] == pytest.approx(0.0028) for item in diagnostics["stages"])
    assert [item["status"] for item in diagnostics["stages"]] == [
        "SUCCEEDED",
        "SUCCEEDED",
        "SUCCEEDED",
    ]

    state_at_chapter = client.get(
        f"/api/analysis-runs/{run['id']}/state-at-chapter?chapter_ordinal=2"
    )
    assert state_at_chapter.status_code == 200
    state_payload = state_at_chapter.json()
    assert state_payload["chapter_title"].startswith("第二章")
    assert state_payload["states"][0]["chapter_ordinal"] == 2
    assert state_payload["knowledge"][0]["actor"] == "林舟"

    completed_task = client.get(f"/api/tasks/{claim.id}").json()
    artifact = client.get(
        f"/api/artifacts/{completed_task['result_artifact_id']}/content"
    ).json()
    assert artifact["request"]["prompt_id"] == "entities_events"
    assert artifact["request"]["prompt_version"] == "1.2.0"
    assert artifact["request"]["source_version_id"] == version_id
    assert len(artifact["request"]["input_sha256"]) == 64
    assert "参与事件的人物" in artifact["request"]["instructions"]

    entities = client.get(f"/api/analysis-runs/{run['id']}/entities").json()
    events = client.get(f"/api/analysis-runs/{run['id']}/events").json()
    assert [item["name"] for item in entities] == ["林舟"]
    assert [item["title"] for item in events] == ["林舟发现密信"]
    assert entities[0]["status"] == "VALID"
    assert events[0]["status"] == "VALID"

    workbench = client.get(f"/api/analysis-runs/{run['id']}/workbench")
    assert workbench.status_code == 200
    projection = workbench.json()
    assert projection["narrative_status"] == "READY"
    assert projection["story_overview"]["protagonist"] == "林舟"
    assert len(projection["story_overview"]["development_path"]) == 3
    assert projection["story_overview"]["current_result"].startswith("林舟掌握了密信")
    assert [item["name"] for item in projection["characters"]] == ["林舟"]
    assert projection["characters"][0]["role"] == "PROTAGONIST"
    assert projection["characters"][0]["identities"] == ["旧宅归来者"]
    assert projection["characters"][0]["important_experiences"] == ["雨夜发现密信"]
    assert projection["characters"][0]["arc_summary"] == "从被动发现转向主动追查。"
    assert projection["events"][0]["people"] == ["林舟"]
    assert projection["events"][0]["chapter_titles"] == ["第一章 归来"]
    assert projection["events"][0]["narrative_mode"] == "ACTUAL"
    assert projection["events"][0]["location"] == "旧宅"
    assert projection["events"][0]["trigger"] == "林舟回到旧宅并进入房间。"
    assert projection["events"][0]["outcome"] == "林舟确认有人专门给自己留下了密信。"
    assert projection["events"][0]["boundary_status"] == "EXACT_SPAN"
    assert len(projection["phases"]) == 1
    assert projection["phases"][0]["event_ids"] == [projection["events"][0]["id"]]
    assert projection["phases"][0]["title"] == "雨夜归来与密信出现"
    assert projection["deep_status"] == "READY"
    assert projection["deep_analysis"]["fact_versions"][0]["status"] == "CONFIRMED"
    assert projection["deep_analysis"]["claims"][0]["verification_status"] == "SUPPORTED"
    assert projection["deep_analysis"]["world_rules"][0]["discovered_chapter"] == 1

    issue = client.post(
        f"/api/analysis-runs/{run['id']}/issues",
        json={
            "target_kind": "FACT",
            "target_id": projection["deep_analysis"]["fact_versions"][0]["id"],
            "target_label": "林舟当前目标",
            "category": "UNCLEAR",
            "note": "目标描述需要更具体。",
        },
    )
    assert issue.status_code == 201
    assert issue.json()["status"] == "OPEN"
    recompute = client.post(f"/api/analysis-runs/{run['id']}/deep/recompute")
    assert recompute.status_code == 202
    assert recompute.json()["status"] == "PENDING"

    with client.app.state.session_factory() as session:
        revision_claim = claim_next_task(
            session,
            worker_id="deep-revision-test-worker",
            lease_seconds=60,
        )
    assert revision_claim is not None
    assert revision_claim.kind == "analysis.deep_insights"
    revision_payload = json.loads(revision_claim.payload_json)
    assert revision_payload["revision_scope"] == [
        "fact_versions",
        "state_changes",
        "actor_knowledge",
        "claims",
    ]
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        revision_claim,
        registry,
    )
    issues = client.get(f"/api/analysis-runs/{run['id']}/issues").json()
    assert issues[0]["status"] == "RESOLVED"
    revisions = client.get(f"/api/analysis-runs/{run['id']}/deep/revisions").json()
    assert [item["revision_no"] for item in revisions] == [1, 2]
    diff = client.get(f"/api/analysis-runs/{run['id']}/deep/diff").json()
    assert diff["from_revision"] == 1
    assert diff["to_revision"] == 2
    assert diff["changed_counts"]["fact_versions"] == 1
    first_revision = client.get(
        f"/api/analysis-runs/{run['id']}/workbench?deep_revision=1"
    )
    assert first_revision.status_code == 200
    assert first_revision.json()["deep_revision"] == 1
    latest_revision = client.get(f"/api/analysis-runs/{run['id']}/workbench")
    assert latest_revision.json()["deep_revision"] == 2
    assert latest_revision.json()["deep_analysis"]["conflicts"][0]["resolution"] == "尚未解决。"
    missing_revision = client.get(
        f"/api/analysis-runs/{run['id']}/workbench?deep_revision=999"
    )
    assert missing_revision.status_code == 404
    assert missing_revision.json()["detail"]["code"] == "DEEP_ANALYSIS_REVISION_NOT_FOUND"

    evidence = client.get(f"/api/evidence/{events[0]['evidence_ids'][0]}")
    assert evidence.status_code == 200
    assert "桌上放着一封写着他名字的密信" in evidence.json()["context_text"]
    assert evidence.json()["chapter_title"] == "第一章 归来"

    with client.app.state.session_factory() as session:
        task = session.scalar(select(Task).where(Task.id == claim.id))
        assert task is not None
        output = parse_provider_output(ANALYSIS_OUTPUT)
        persist_analysis_output(
            session,
            client.app.state.settings,
            task=task,
            attempt_id=claim.current_attempt_id,
            task_payload=json.loads(task.payload_json),
            output=output,
        )
        assert session.scalar(select(func.count(EntityCandidate.id))) == 2
        assert session.scalar(select(func.count(EventCandidate.id))) == 1

    confirmed = client.post(f"/api/analysis-runs/{run['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"

    character_issue = client.post(
        f"/api/analysis-runs/{run['id']}/issues",
        json={
            "target_kind": "CHARACTER",
            "target_id": projection["characters"][0]["id"],
            "target_label": "林舟",
            "category": "UNCLEAR",
            "note": "角色定位需要重新核对。",
        },
    )
    assert character_issue.status_code == 201
    narrative_recompute = client.post(f"/api/analysis-runs/{run['id']}/deep/recompute")
    assert narrative_recompute.status_code == 202
    with client.app.state.session_factory() as session:
        narrative_task = session.scalar(
            select(Task)
            .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
            .where(
                AnalysisRunTask.run_id == run["id"],
                Task.kind == "analysis.narrative_synthesis",
                Task.status == TaskStatus.PENDING.value,
            )
            .order_by(Task.created_at.desc())
        )
        assert narrative_task is not None
        assert "CHARACTER" in narrative_task.payload_json


def test_invalid_model_structure_records_safe_field_diagnostics(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-test"})
    version_id = imported["version"]["id"]
    run = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    ).json()

    registry = ProviderRegistry([InvalidStructureProvider()])
    with client.app.state.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="invalid-structure-worker",
            lease_seconds=60,
        )
    assert claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        claim,
        registry,
    )

    diagnostics = client.get(
        f"/api/analysis-runs/{run['id']}/diagnostics"
    )
    assert diagnostics.status_code == 200
    payload = diagnostics.json()
    stage = payload["stages"][0]
    assert stage["attempt_count"] == 1
    assert stage["prompt_tokens"] == 42
    assert stage["completion_tokens"] == 17
    assert "置信度" in stage["latest_error"]
    assert "自动重试" in stage["latest_error"]

    with client.app.state.session_factory() as session:
        attempt = session.scalar(
            select(TaskAttempt).where(TaskAttempt.task_id == claim.id)
        )
        assert attempt is not None
        stored = json.loads(attempt.diagnostics_json)
        assert stored["phase"] == "schema_validation"
        assert stored["validation_error_count"] == 1
        assert stored["model"] == "test-model"
    assert "raw_text" not in stored


def test_incomplete_legacy_narrative_is_blocked_and_can_be_repaired(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-test"})
    version_id = imported["version"]["id"]
    run = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    ).json()
    registry = ProviderRegistry([StaticAnalysisProvider()])

    with client.app.state.session_factory() as session:
        foundation_claim = claim_next_task(
            session, worker_id="legacy-foundation-worker", lease_seconds=60
        )
    assert foundation_claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        foundation_claim,
        registry,
    )
    with client.app.state.session_factory() as session:
        narrative_claim = claim_next_task(
            session, worker_id="legacy-narrative-worker", lease_seconds=60
        )
    assert narrative_claim is not None
    assert narrative_claim.kind == "analysis.narrative_synthesis"
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        narrative_claim,
        registry,
    )

    with client.app.state.session_factory() as session:
        synthesis = session.scalar(
            select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run["id"])
        )
        assert synthesis is not None
        payload = json.loads(synthesis.payload_json)
        current_projection = client.get(
            f"/api/analysis-runs/{run['id']}/workbench"
        ).json()
        current_event = current_projection["events"][0]
        normalized_title = re.sub(r"\s+", "", current_event["title"]).casefold()
        legacy_identity = f"{run['id']}:{normalized_title}:{current_event['event_type']}"
        legacy_event_id = f"cev_{hashlib.sha256(legacy_identity.encode('utf-8')).hexdigest()[:32]}"
        payload["narrative_phases"][0]["event_ids"] = [legacy_event_id]
        payload["character_roles"] = []
        synthesis.payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        session.commit()

    workbench = client.get(f"/api/analysis-runs/{run['id']}/workbench")
    assert workbench.status_code == 200
    assert workbench.json()["narrative_status"] == "INCOMPLETE"
    assert workbench.json()["characters"][0]["role"] == "UNCLASSIFIED"
    assert workbench.json()["phases"][0]["event_ids"] == [current_event["id"]]
    assert workbench.json()["phases"][0]["chapter_ordinals"]

    blocked = client.post(f"/api/analysis-runs/{run['id']}/confirm")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "NARRATIVE_SYNTHESIS_INCOMPLETE"

    repair = client.post(f"/api/analysis-runs/{run['id']}/narrative/repair")
    assert repair.status_code == 202
    assert repair.json()["status"] == "PENDING"
    with client.app.state.session_factory() as session:
        repair_claim = claim_next_task(
            session, worker_id="legacy-repair-worker", lease_seconds=60
        )
    assert repair_claim is not None
    assert repair_claim.kind == "analysis.narrative_synthesis"
    assert "人物角色覆盖" in repair_claim.payload_json


def test_running_or_stale_attempt_candidates_are_not_visible(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-test"})
    version_id = imported["version"]["id"]
    run = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    ).json()

    with client.app.state.session_factory() as session:
        claim = claim_next_task(session, worker_id="old-worker", lease_seconds=60)
    assert claim is not None
    with client.app.state.session_factory() as session:
        task = session.get(Task, claim.id)
        assert task is not None
        persist_analysis_output(
            session,
            client.app.state.settings,
            task=task,
            attempt_id=claim.current_attempt_id,
            task_payload=json.loads(task.payload_json),
            output=parse_provider_output(ANALYSIS_OUTPUT),
        )

    assert client.get(f"/api/analysis-runs/{run['id']}/entities").json() == []
    assert client.get(f"/api/analysis-runs/{run['id']}/events").json() == []


def test_analysis_failure_exposes_plain_reason_and_can_start_again(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-invalid"})
    version_id = imported["version"]["id"]
    run = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    ).json()
    with client.app.state.session_factory() as session:
        claim = claim_next_task(session, worker_id="auth-failure-worker", lease_seconds=60)
    assert claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        claim,
        ProviderRegistry([AuthenticationFailureProvider()]),
    )

    failed = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    ).json()
    assert failed["id"] == run["id"]
    assert failed["status"] == "FAILED"
    assert failed["failure_code"] == "PROVIDER_AUTH_FAILED"
    assert failed["failure_message"] == "API Key 无效或没有使用该模型的权限。"

    restarted = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    )
    assert restarted.status_code == 201
    assert restarted.json()["id"] != run["id"]


def _provider_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'provider.db').as_posix()}",
        workspace_dir=tmp_path / "workspace",
        openai_api_key="sk-test",
        openai_timeout_seconds=1,
    )
    settings.ensure_directories()
    return settings


def _provider_payload() -> dict:
    return {
        "instructions": "只返回结构化结果。",
        "input": "第一章\n林舟回来了。",
        "output_schema": {
            "type": "object",
            "properties": {"entities": {"type": "array"}, "events": {"type": "array"}},
            "required": ["entities", "events"],
            "additionalProperties": False,
        },
    }


def _run_provider(provider: OpenAIResponsesProvider) -> ProviderResponse:
    return asyncio.run(provider.complete(task_kind="analysis.entities_events", payload=_provider_payload()))


@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (401, "PROVIDER_AUTH_FAILED", False),
        (403, "PROVIDER_AUTH_FAILED", False),
        (429, "PROVIDER_RATE_LIMITED", True),
        (500, "PROVIDER_UNAVAILABLE", True),
        (400, "PROVIDER_BAD_REQUEST", False),
    ],
)
def test_openai_http_failures_have_stable_contract(
    tmp_path: Path,
    status_code: int,
    expected_code: str,
    retryable: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"retry-after": "17"} if status_code == 429 else {}
        return httpx.Response(status_code, headers=headers, json={"error": "test"})

    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError) as caught:
        _run_provider(provider)
    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable
    if status_code == 429:
        assert caught.value.retry_after_seconds == 17


def test_openai_timeout_is_retryable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("test timeout", request=request)

    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError) as caught:
        _run_provider(provider)
    assert caught.value.code == "PROVIDER_TIMEOUT"
    assert caught.value.retryable is True


def test_openai_invalid_output_is_retryable(tmp_path: Path) -> None:
    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"output": []})),
    )

    with pytest.raises(ProviderError) as caught:
        _run_provider(provider)
    assert caught.value.code == "PROVIDER_INVALID_OUTPUT"
    assert caught.value.retryable is True


def test_openai_structured_output_is_parsed(tmp_path: Path) -> None:
    output_text = json.dumps({"entities": [], "events": []})
    response_body = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": output_text}],
            }
        ],
        "usage": {"input_tokens": 23, "output_tokens": 11},
    }
    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response_body)
        ),
    )

    result = _run_provider(provider)

    assert result.parsed == {"entities": [], "events": []}
    assert result.prompt_tokens == 23
    assert result.completion_tokens == 11


def test_provider_removes_schema_document_metadata_before_request(tmp_path: Path) -> None:
    output_text = json.dumps({"entities": [], "events": []})

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        wire_schema = body["text"]["format"]["schema"]
        assert "$schema" not in wire_schema
        assert "$id" not in wire_schema
        assert wire_schema["title"] == "Novel extraction result"
        assert "title" in wire_schema["properties"]
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": output_text}],
                    }
                ]
            },
        )

    payload = _provider_payload()
    payload["output_schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "novel-result.schema.json",
        "title": "Novel extraction result",
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "entities": {"type": "array"},
            "events": {"type": "array"},
        },
        "required": ["entities", "events"],
        "additionalProperties": False,
    }
    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        provider.complete(task_kind="analysis.entities_events", payload=payload)
    )

    assert result.parsed == {"entities": [], "events": []}


def test_compatible_provider_removes_schema_document_metadata_before_request(tmp_path: Path) -> None:
    settings = _provider_settings(tmp_path)
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="兼容接口",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://provider.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="人物与事件精确提取",
        service_id=service.id,
        model="gemini-compatible",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=30,
        max_retries=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        wire_schema = body["response_format"]["json_schema"]["schema"]
        assert "$schema" not in wire_schema
        assert "$id" not in wire_schema
        assert wire_schema["title"] == "Novel extraction result"
        assert "title" in wire_schema["properties"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"entities": [], "events": []}'}}]},
        )

    payload = _provider_payload()
    payload["model_profile_id"] = ENTITIES_EVENTS_PROFILE_ID
    payload["output_schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "novel-result.schema.json",
        "title": "Novel extraction result",
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "entities": {"type": "array"},
            "events": {"type": "array"},
        },
        "required": ["entities", "events"],
        "additionalProperties": False,
    }
    provider = OpenAIResponsesProvider(
        settings,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        provider.complete(task_kind="analysis.entities_events", payload=payload)
    )

    assert result.parsed == {"entities": [], "events": []}


def test_compatible_provider_uses_locally_validated_json_for_complex_analysis(tmp_path: Path) -> None:
    settings = _provider_settings(tmp_path)
    service = save_model_service(
        settings,
        service_id=None,
        name="复杂结构兼容接口",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://provider.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="人物与事件精确提取",
        service_id=service.id,
        model="gemini-compatible",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=30,
        max_retries=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "response_format" not in body
        assert "输出必须是 JSON 对象" in body["messages"][0]["content"]
        assert '"entities"' in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"entities": [], "events": []}'}}]},
        )

    payload = _provider_payload()
    payload["model_profile_id"] = ENTITIES_EVENTS_PROFILE_ID
    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handler))

    result = asyncio.run(
        provider.complete(task_kind="analysis.narrative_synthesis", payload=payload)
    )

    assert result.parsed == {"entities": [], "events": []}
    assert result.parameters["structured_output"] == "JSON_ONLY"


def test_provider_exposes_short_upstream_bad_request_reason(tmp_path: Path) -> None:
    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={"error": {"message": 'Unknown name "$id" at response_schema'}},
            )
        ),
    )

    with pytest.raises(ProviderError) as caught:
        _run_provider(provider)

    assert caught.value.code == "PROVIDER_BAD_REQUEST"
    assert 'Unknown name "$id"' in str(caught.value)
