from __future__ import annotations

import json

from sqlalchemy import select

from app.models import (
    AnalysisRun,
    AnalysisRunStatus,
    DeepAnalysis,
    EntityCandidate,
    EvidenceSpan,
    NarrativeSynthesis,
    Task,
    TaskStatus,
)
from app.repositories import claim_next_task


def _review_run_with_identity_candidate(client) -> str:
    project = client.post("/api/projects", json={"name": "人物身份裁决"}).json()
    imported = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=identity.txt",
        content=(
            "第一章 校长\n"
            "昂热走进会议室。\n"
            "第二章 全名\n"
            "希尔伯特·让·昂热在档案上签字。"
        ).encode("utf-8"),
    ).json()
    version_id = imported["version"]["id"]
    assert client.post(f"/api/source-versions/{version_id}/confirm").status_code == 200
    client.put("/api/settings/openai", json={"api_key": "sk-test"})
    run = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    ).json()

    with client.app.state.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="identity-decision-test",
            lease_seconds=60,
        )
        assert claim is not None
        task = session.get(Task, claim.id)
        analysis_run = session.get(AnalysisRun, run["id"])
        evidence_ids = list(session.scalars(
            select(EvidenceSpan.id)
            .where(EvidenceSpan.source_version_id == version_id)
            .order_by(EvidenceSpan.start_char)
        ))
        assert task is not None
        assert analysis_run is not None
        assert task.current_attempt_id is not None
        task.status = TaskStatus.SUCCEEDED.value
        analysis_run.status = AnalysisRunStatus.REVIEW.value
        for index, (name, aliases, evidence_id) in enumerate([
            ("昂热", [], evidence_ids[0]),
            ("希尔伯特·让·昂热", ["昂热"], evidence_ids[-1]),
        ]):
            session.add(EntityCandidate(
                id=f"enc_identity_{index}",
                run_id=run["id"],
                source_version_id=version_id,
                name=name,
                normalized_name=name.casefold(),
                entity_type="PERSON",
                aliases_json=json.dumps(aliases, ensure_ascii=False),
                description=f"{name}的人物候选。",
                evidence_ids_json=json.dumps([evidence_id]),
                status="VALID",
                confidence=100,
                created_by_task_id=task.id,
                created_by_attempt_id=task.current_attempt_id,
            ))
        session.add(NarrativeSynthesis(
            run_id=run["id"],
            source_version_id=version_id,
            payload_json=json.dumps({
                "story_overview": {
                    "premise": "校长以不同名字出现。",
                    "synopsis": "昂热和全名身份需要人工核对。",
                    "protagonist": "昂热",
                    "protagonist_goal": "主持会议",
                    "central_conflict": "身份称呼不统一",
                    "current_situation": "等待身份确认",
                    "unresolved_questions": [],
                    "evidence_ids": evidence_ids[:1],
                },
                "character_roles": [
                    {
                        "name": "昂热",
                        "role": "CORE_SUPPORTING",
                        "role_reason": "主持会议。",
                    },
                    {
                        "name": "希尔伯特·让·昂热",
                        "role": "MINOR",
                        "role_reason": "在档案上签字。",
                    },
                ],
                "character_relations": [],
                "narrative_phases": [],
                "event_relations": [],
            }, ensure_ascii=False),
            prompt_id="identity-test",
            prompt_version="1.0",
            created_by_task_id=task.id,
            created_by_attempt_id=task.current_attempt_id,
        ))
        session.add(DeepAnalysis(
            run_id=run["id"],
            source_version_id=version_id,
            revision_no=1,
            payload_json=json.dumps({
                "fact_versions": [],
                "state_changes": [],
                "actor_knowledge": [],
                "knowledge_transfers": [],
                "world_rules": [],
                "foreshadowing": [],
                "conflicts": [],
                "scene_analysis": [],
                "claims": [],
                "entity_resolutions": [],
            }, ensure_ascii=False),
            prompt_id="identity-test",
            prompt_version="1.0",
            created_by_task_id=task.id,
            created_by_attempt_id=task.current_attempt_id,
        ))
        session.commit()
    return run["id"]


def test_person_identity_decision_is_reversible_and_confirmation_safe(client) -> None:
    run_id = _review_run_with_identity_candidate(client)

    initial = client.get(f"/api/analysis-runs/{run_id}/workbench").json()
    assert [item["name"] for item in initial["characters"]] == [
        "希尔伯特·让·昂热",
        "昂热",
    ]
    assert len(initial["person_identity_candidates"]) == 1
    candidate = initial["person_identity_candidates"][0]
    assert candidate["recommended_name"] == "昂热"
    blocked_confirmation = client.post(
        f"/api/analysis-runs/{run_id}/confirm"
    )
    assert blocked_confirmation.status_code == 409
    assert (
        blocked_confirmation.json()["detail"]["code"]
        == "PERSON_IDENTITY_REVIEW_REQUIRED"
    )

    merged = client.post(
        f"/api/analysis-runs/{run_id}/person-identity-decisions",
        json={
            "candidate_key": candidate["candidate_key"],
            "decision": "SAME",
        },
    )
    assert merged.status_code == 200
    merged_payload = merged.json()
    assert [item["name"] for item in merged_payload["characters"]] == ["昂热"]
    assert merged_payload["characters"][0]["aliases"] == ["希尔伯特·让·昂热"]
    assert merged_payload["person_identity_candidates"] == []
    decision = merged_payload["person_identity_decisions"][0]
    assert decision["decision"] == "SAME"

    undone = client.delete(
        f"/api/person-identity-decisions/{decision['id']}"
    )
    assert undone.status_code == 200
    undone_payload = undone.json()
    assert len(undone_payload["characters"]) == 2
    assert len(undone_payload["person_identity_candidates"]) == 1

    separated = client.post(
        f"/api/analysis-runs/{run_id}/person-identity-decisions",
        json={
            "candidate_key": candidate["candidate_key"],
            "decision": "DIFFERENT",
        },
    )
    assert separated.status_code == 200
    separated_payload = separated.json()
    assert len(separated_payload["characters"]) == 2
    assert separated_payload["person_identity_candidates"] == []
    separate_decision = separated_payload["person_identity_decisions"][0]

    confirmed = client.post(f"/api/analysis-runs/{run_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"
    rejected = client.delete(
        f"/api/person-identity-decisions/{separate_decision['id']}"
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "ANALYSIS_ALREADY_CONFIRMED"
