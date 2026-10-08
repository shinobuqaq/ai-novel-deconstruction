from __future__ import annotations

from copy import deepcopy

import pytest

from app.evaluation import (
    EvaluationError,
    build_gold_template,
    choose_sample_window,
    score_gold,
    validate_gold,
)


def _workbench() -> dict:
    return {
        "run_id": "run_test",
        "source_version_id": "src_test",
        "characters": [
            {
                "id": "chr_1",
                "name": "林舟",
                "aliases": ["小舟"],
                "evidence_ids": ["evd_1"],
            },
            {
                "id": "chr_2",
                "name": "周岚",
                "aliases": [],
                "evidence_ids": ["evd_2"],
            },
        ],
        "person_identity_candidates": [
            {
                "candidate_key": "pic_1",
                "left_name": "林舟",
                "right_name": "林船",
                "recommended_decision": "SAME",
                "review_priority": "BLOCKING",
                "cooccurrence_count": 0,
                "reason": "直接别名",
                "evidence_ids": ["evd_1"],
            },
            {
                "candidate_key": "pic_2",
                "left_name": "林舟",
                "right_name": "周岚",
                "recommended_decision": "DIFFERENT",
                "review_priority": "OPTIONAL",
                "cooccurrence_count": 2,
                "reason": "共同事件",
                "evidence_ids": ["evd_2"],
            },
        ],
        "person_identity_decisions": [],
        "events": [
            {
                "id": "evt_1",
                "title": "林舟回家",
                "start_char": 100,
                "end_char": 180,
                "chapter_ordinals": [1],
                "evidence_ids": ["evd_1"],
            },
            {
                "id": "evt_2",
                "title": "跨章追踪",
                "start_char": 49_900,
                "end_char": 50_100,
                "chapter_ordinals": [2, 3],
                "evidence_ids": ["evd_2"],
            },
        ],
        "deep_analysis": {
            "fact_versions": [
                {
                    "id": "fact_1",
                    "subject": "林舟",
                    "predicate": "所在",
                    "value": "旧宅",
                    "valid_from_chapter": 1,
                    "valid_to_chapter": 2,
                    "timeline_status": "EXPIRED",
                    "evidence_ids": ["evd_1"],
                    "counter_evidence_ids": [],
                }
            ]
        },
    }


def _evidence() -> dict:
    return {
        "evd_1": {
            "id": "evd_1",
            "chapter_title": "第一章",
            "start_char": 100,
            "end_char": 120,
            "text_snapshot": "林舟回到旧宅。",
            "context_text": "林舟回到旧宅。",
        },
        "evd_2": {
            "id": "evd_2",
            "chapter_title": "第三章",
            "start_char": 49_900,
            "end_char": 49_930,
            "text_snapshot": "林舟与周岚一起出现。",
            "context_text": "林舟与周岚一起出现。",
        },
    }


def test_choose_sample_window_prefers_identity_and_temporal_coverage() -> None:
    start, end, coverage = choose_sample_window(_workbench(), _evidence(), 50_000)

    assert end - start == 50_000
    assert coverage["person_pairs"] >= 1
    assert coverage["facts"] == 1
    assert coverage["temporal_facts"] == 1


def test_gold_template_is_draft_and_keeps_source_evidence() -> None:
    template = build_gold_template(
        _workbench(),
        _evidence(),
        sample_start=0,
        sample_chars=50_000,
    )

    assert template["metadata"]["annotation_status"] == "DRAFT"
    assert template["person_identity_pairs"][0]["gold_label"] == "PENDING"
    assert template["person_identity_pairs"][0]["evidence"][0]["text_snapshot"] == "林舟回到旧宅。"
    assert template["gold_events"][0]["source_event_id"] == "evt_1"
    assert template["gold_facts"][0]["source_fact_id"] == "fact_1"


def test_gold_template_keeps_completed_user_identity_decision() -> None:
    workbench = _workbench()
    workbench["person_identity_candidates"] = []
    workbench["person_identity_decisions"] = [{
        "candidate_key": "pic_decided",
        "left_name": "林舟",
        "right_name": "小舟",
        "decision": "SAME",
    }]

    template = build_gold_template(
        workbench,
        _evidence(),
        sample_start=0,
        sample_chars=50_000,
    )

    pair = template["person_identity_pairs"][0]
    assert pair["gold_label"] == "SAME"
    assert pair["review_status"] == "CONFIRMED"
    assert pair["evidence"][0]["id"] == "evd_1"


def test_validation_rejects_draft_and_insufficient_second_review() -> None:
    template = build_gold_template(
        _workbench(),
        _evidence(),
        sample_start=0,
        sample_chars=50_000,
    )
    template["metadata"]["annotation_status"] = "READY"
    pair = template["person_identity_pairs"][0]
    pair["gold_label"] = "SAME"
    pair["review_status"] = "CONFIRMED"
    fact = template["gold_facts"][0]
    fact["gold_label"] = "CORRECT"
    fact["review_status"] = "CONFIRMED"

    validation = validate_gold(template)

    assert not validation["valid"]
    assert any("二次复核不足" in message for message in validation["errors"])
    assert any("至少需要 20 条" in message for message in validation["errors"])


def test_score_reports_identity_event_and_fact_metrics() -> None:
    template = build_gold_template(
        _workbench(),
        _evidence(),
        sample_start=0,
        sample_chars=50_000,
    )
    template["metadata"]["annotation_status"] = "READY"
    pair = template["person_identity_pairs"][0]
    pair["gold_label"] = "SAME"
    pair["review_status"] = "SECOND_REVIEWED"
    pair["second_review_status"] = "CONFIRMED"
    event = template["gold_events"][0]
    event["review_status"] = "CONFIRMED"
    fact = template["gold_facts"][0]
    fact["gold_label"] = "CORRECT"
    fact["review_status"] = "SECOND_REVIEWED"
    fact["second_review_status"] = "CONFIRMED"

    prediction = deepcopy(_workbench())
    prediction["characters"][0]["aliases"].extend(["林船"])
    report = score_gold(template, prediction, allow_draft=True)

    assert report["person_identity"]["precision"] == 1
    assert report["person_identity"]["recall"] == 1
    assert report["person_identity"]["candidate_recall"] == 1
    assert report["events"]["discovery_recall"] == 1
    assert report["facts"]["correctness"] == 1


def test_score_rejects_another_run() -> None:
    template = build_gold_template(
        _workbench(),
        _evidence(),
        sample_start=0,
        sample_chars=50_000,
    )
    template["metadata"]["annotation_status"] = "READY"
    template["metadata"]["minimum_second_review_ratio"] = 0
    prediction = deepcopy(_workbench())
    prediction["run_id"] = "run_other"

    with pytest.raises(EvaluationError, match="run_id"):
        score_gold(template, prediction, allow_draft=True)
