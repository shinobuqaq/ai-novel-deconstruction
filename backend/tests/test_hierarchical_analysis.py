from __future__ import annotations

import json

from sqlalchemy import select

from app.models import AnalysisDigest, AnalysisDigestLevel, AnalysisRunTask, Task
from app.providers.base import ProviderResponse
from app.providers.registry import ProviderRegistry
from app.repositories import claim_next_task
from app.services.analysis import provider_payload_for_narrative_synthesis
from app.services.provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    save_analysis_profile,
    save_model_service,
)
from app.services.tasks import execute_task_sync


class HierarchyProvider:
    name = "openai"

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        if task_kind == "analysis.entities_events":
            output = {"entities": [], "events": []}
        else:
            assert task_kind == "analysis.hierarchical_digest"
            source = json.loads(payload["input"])
            chapter_range = source["chapter_range"]
            output = {
                "title": f"第 {chapter_range['start']}-{chapter_range['end']} 章",
                "summary": "这一连续范围已经按原文顺序整理，并保留向下来源链。",
                "situation": "范围内故事继续发展。",
                "goal": "保持章节覆盖。",
                "obstacle": "长篇材料无法一次放入最终请求。",
                "key_actions": ["按连续章节范围整理材料"],
                "outcome": "形成可供上层聚合的导航摘要。",
                "change": "上层不再需要直接承载全部原文。",
                "next_hook": "继续进入下一连续范围。",
                "character_progressions": [],
                "event_ids": [],
                "evidence_ids": [],
            }
        raw = json.dumps(output, ensure_ascii=False)
        return ProviderResponse(
            raw_text=raw,
            parsed=output,
            prompt_tokens=100,
            completion_tokens=50,
            provider_id="hierarchy-test",
            model="hierarchy-test-model",
        )


def _long_novel() -> str:
    chapters: list[str] = []
    for number in range(1, 13):
        paragraph = f"这是第{number}章的连续正文，人物沿着本章目标继续行动并留下新的局面。"
        chapters.append(f"第{number}章 章节{number}\n" + (paragraph * 340))
    return "\n".join(chapters)


def test_long_book_builds_source_linked_range_and_stage_digests(client) -> None:
    project = client.post("/api/projects", json={"name": "长篇分层测试"}).json()
    imported = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=long.txt",
        content=_long_novel().encode("utf-8"),
    )
    assert imported.status_code == 201
    version = imported.json()["version"]
    assert version["total_chars"] > 120_000
    assert client.post(f"/api/source-versions/{version['id']}/confirm").status_code == 200

    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="长篇分层测试服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://provider.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="长篇分层测试方案",
        service_id=service.id,
        model="hierarchy-test-model",
        temperature=None,
        max_output_tokens=4_000,
        reasoning_effort="auto",
        timeout_seconds=60,
        max_retries=1,
        context_window_tokens=32_000,
    )

    estimate = client.get(
        f"/api/source-versions/{version['id']}/analysis/entities-events/estimate"
    ).json()
    assert estimate["planned_call_count"] > estimate["batch_count"] + 2

    started = client.post(
        f"/api/source-versions/{version['id']}/analysis/entities-events/start"
    )
    assert started.status_code == 201
    run = started.json()
    registry = ProviderRegistry([HierarchyProvider()])

    narrative_claim = None
    for index in range(100):
        with client.app.state.session_factory() as session:
            claim = claim_next_task(
                session,
                worker_id=f"hierarchy-worker-{index}",
                lease_seconds=60,
            )
        assert claim is not None
        if claim.kind == "analysis.narrative_synthesis":
            narrative_claim = claim
            break
        assert claim.kind in {
            "analysis.entities_events",
            "analysis.hierarchical_digest",
        }
        assert execute_task_sync(
            client.app.state.session_factory,
            settings,
            claim,
            registry,
        )

    assert narrative_claim is not None
    with client.app.state.session_factory() as session:
        digests = list(session.scalars(
            select(AnalysisDigest)
            .where(AnalysisDigest.run_id == run["id"])
            .order_by(AnalysisDigest.level, AnalysisDigest.sequence_no)
        ))
        range_digests = [
            item for item in digests if item.level == AnalysisDigestLevel.RANGE.value
        ]
        stage_digests = [
            item for item in digests if item.level == AnalysisDigestLevel.STAGE.value
        ]
        assert len(range_digests) >= 2
        assert stage_digests
        assert range_digests[0].start_chapter == 1
        assert range_digests[-1].end_chapter == 12
        assert all(json.loads(item.source_unit_ids_json) for item in range_digests)
        assert all(json.loads(item.source_digest_ids_json) for item in stage_digests)
        narrative_task = session.get(Task, narrative_claim.id)
        assert narrative_task is not None
        payload = provider_payload_for_narrative_synthesis(
            session,
            settings,
            json.loads(narrative_task.payload_json),
        )
        final_input = json.loads(payload["input"])

    assert final_input["hierarchical_digests"]
    assert all(
        item["authority"] == "DERIVED_NAVIGATION_ONLY"
        for item in final_input["hierarchical_digests"]
    )
    assert final_input["hierarchical_digests"][0]["start_chapter"] == 1
    assert final_input["hierarchical_digests"][-1]["end_chapter"] == 12

    diagnostics = client.get(f"/api/analysis-runs/{run['id']}/diagnostics").json()
    hierarchy_stage = next(
        item for item in diagnostics["stages"]
        if item["key"] == "analysis.hierarchical_digest"
    )
    assert hierarchy_stage["status"] == "SUCCEEDED"
    assert hierarchy_stage["task_count"] == len(range_digests) + len(stage_digests)
