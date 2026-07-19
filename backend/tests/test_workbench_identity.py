from __future__ import annotations

import json

from app.models import EntityCandidate
from app.services.workbench import (
    _annotate_fact_timeline,
    _canonical_person,
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
