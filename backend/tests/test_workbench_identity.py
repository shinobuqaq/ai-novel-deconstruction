from __future__ import annotations

import json

from app.models import EntityCandidate, EventCandidate
from app.services.workbench import (
    _apply_person_resolutions,
    _annotate_fact_timeline,
    _canonical_person,
    _event_candidate_groups,
    _person_groups,
)


def _person(name: str, aliases: list[str], confidence: int = 80) -> EntityCandidate:
    normalized = name.casefold()
    return EntityCandidate(
        id=f"enc_{normalized}",
        run_id="run_identity",
        source_version_id="svr_identity",
        name=name,
        normalized_name=normalized,
        entity_type="PERSON",
        aliases_json=json.dumps(aliases, ensure_ascii=False),
        description=f"{name}的候选描述",
        evidence_ids_json="[]",
        status="VALID",
        confidence=confidence,
        created_by_task_id="tsk_identity",
        created_by_attempt_id="att_identity",
    )


def _event(
    event_id: str,
    title: str,
    evidence_ids: list[str],
    *,
    event_type: str = "DISCOVERY",
    narrative_mode: str = "ACTUAL",
) -> EventCandidate:
    return EventCandidate(
        id=event_id,
        run_id="run_identity",
        source_version_id="svr_identity",
        identity_key=event_id,
        title=title,
        event_type=event_type,
        summary=title,
        participants_json='["林舟"]',
        details_json=json.dumps({"narrative_mode": narrative_mode}, ensure_ascii=False),
        evidence_ids_json=json.dumps(evidence_ids, ensure_ascii=False),
        start_char=10,
        end_char=20,
        status="VALID",
        confidence=80,
        created_by_task_id="tsk_identity",
        created_by_attempt_id="att_identity",
    )


def test_direct_person_alias_is_grouped_without_generic_title_merges() -> None:
    groups = _person_groups([
        _person("张妍", ["李老师"], 95),
        _person("李老师", [], 85),
        _person("王明", ["老师"], 90),
        _person("赵启", ["老师"], 88),
    ])

    grouped_names = [sorted(item.name for item in group) for group in groups]
    assert sorted(grouped_names) == [["张妍", "李老师"], ["王明"], ["赵启"]]
    merged = next(group for group in groups if len(group) == 2)
    assert _canonical_person(merged).name == "张妍"


def test_person_aliases_do_not_form_an_unreviewed_transitive_chain() -> None:
    groups = _person_groups([
        _person("甲", ["乙"]),
        _person("乙", ["丙"]),
        _person("丙", []),
    ])

    assert sorted(len(group) for group in groups) == [1, 2]


def test_evidence_backed_person_resolution_is_projection_only_and_reversible() -> None:
    characters = [
        {
            "id": "c1",
            "name": "林舟",
            "aliases": [],
            "description": "雨夜回到旧宅的人。",
            "evidence_ids": ["e1"],
            "event_ids": ["ev1"],
            "first_chapter_ordinal": 1,
            "first_chapter_title": "第一章",
            "last_chapter_ordinal": 1,
            "last_chapter_title": "第一章",
            "appearance_count": 1,
            "activity_level": "低",
            "status": "VALID",
            "confidence": 90,
            "identity_notes": [],
        },
        {
            "id": "c2",
            "name": "沈砚",
            "aliases": [],
            "description": "后来以假身份出现的人。",
            "evidence_ids": ["e2"],
            "event_ids": ["ev2"],
            "first_chapter_ordinal": 2,
            "first_chapter_title": "第二章",
            "last_chapter_ordinal": 2,
            "last_chapter_title": "第二章",
            "appearance_count": 1,
            "activity_level": "低",
            "status": "VALID",
            "confidence": 80,
            "identity_notes": [],
        },
    ]
    events = [
        {"id": "ev1", "people": ["林舟"]},
        {"id": "ev2", "people": ["沈砚"]},
    ]
    resolutions = [{
        "entity_type": "PERSON",
        "canonical_name": "林舟",
        "merged_names": ["林舟", "沈砚"],
        "reason": "第二章明确揭示沈砚使用了林舟的假身份。",
        "evidence_ids": ["e2"],
    }]

    merged = _apply_person_resolutions("run_identity", characters, events, resolutions)

    assert len(merged) == 1
    assert merged[0]["name"] == "林舟"
    assert merged[0]["aliases"] == ["沈砚"]
    assert merged[0]["event_ids"] == ["ev1", "ev2"]
    assert merged[0]["status"] == "UNCERTAIN"
    assert "假身份" in merged[0]["identity_notes"][0]
    assert events[1]["people"] == ["林舟"]

    unchanged = _apply_person_resolutions("run_identity", characters, events, [])
    assert len(unchanged) == 2


def test_event_candidates_with_alternate_titles_need_shared_exact_evidence() -> None:
    groups = _event_candidate_groups([
        _event("e1", "林舟发现密信", ["span-1"]),
        _event("e2", "旧宅桌上出现写给林舟的信", ["span-1"]),
        _event("e3", "林舟再次收到密信", ["span-2"]),
        _event("e4", "回忆中发现密信", ["span-1"], narrative_mode="MEMORY"),
    ])

    grouped_ids = [sorted(item.id for item in group) for _key, group in groups]
    assert ["e1", "e2"] in grouped_ids
    assert ["e3"] in grouped_ids
    assert ["e4"] in grouped_ids


def test_fact_timeline_keeps_expiry_reestablishment_and_conflicts() -> None:
    facts = [
        {"id": "f1", "subject": "门", "predicate": "状态", "value": "关闭", "status": "CONFIRMED", "valid_from_chapter": 1, "valid_to_chapter": 2},
        {"id": "f2", "subject": "门", "predicate": "状态", "value": "开启", "status": "CONFIRMED", "valid_from_chapter": 3, "valid_to_chapter": 4},
        {"id": "f3", "subject": "门", "predicate": "状态", "value": "关闭", "status": "CONFIRMED", "valid_from_chapter": 5, "valid_to_chapter": None},
        {"id": "f4", "subject": "密信", "predicate": "来源", "value": "张妍", "status": "CONFIRMED", "valid_from_chapter": 2, "valid_to_chapter": None},
        {"id": "f5", "subject": "密信", "predicate": "来源", "value": "未知人物", "status": "REPORTED", "valid_from_chapter": 2, "valid_to_chapter": 3},
    ]

    _annotate_fact_timeline(facts)

    by_id = {item["id"]: item for item in facts}
    assert by_id["f1"]["timeline_status"] == "EXPIRED"
    assert by_id["f2"]["timeline_status"] == "EXPIRED"
    assert by_id["f3"]["timeline_status"] == "REESTABLISHED"
    assert by_id["f4"]["timeline_status"] == "CONFLICTING"
    assert by_id["f5"]["timeline_status"] == "CONFLICTING"
