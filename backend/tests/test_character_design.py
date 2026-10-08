from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

import app.services.character_design as character_design_service
from app.services.character_design import (
    CHARACTER_DESIGN_FIELDS,
    CHARACTER_DESIGN_PROMPT_VERSION,
    CharacterDesignValidationError,
    _character_design_sources_ready,
    _claim_is_grounded,
    _claim_is_strictly_grounded,
    _compiled_arc_summary,
    _compiled_field_explanation,
    _contains_earlier_same_named_target_commitment,
    _contains_earlier_distinctive_repeat,
    _evidence_overlap_ngrams,
    _first_display_repeat_guardrails,
    _merge_event_support_ids,
    _needs_contrast_context,
    _protagonist,
    _prompt,
    _rank_decision_support,
    _raise_for_references,
    _request_budget_chars,
    character_design_source_fingerprint,
    parse_character_design_evidence,
    provider_payload_for_character_design,
    reconcile_character_design_response,
)


FIELD_GROUNDING_TEXT = (
    "林舟发现写着自己名字的密信后，决定尽快找到寄信人，"
    "弄清密信来意，也想掌握自己的处境。他警惕未知风险，"
    "但确认线索后主动追查；没有证据时不把猜测当成事实，"
    "并从零散线索中确定下一步行动。"
)


def test_prompt_semver_matches_service_version() -> None:
    assert f"semver: `{CHARACTER_DESIGN_PROMPT_VERSION}`" in _prompt()


def _projection() -> dict:
    return {
        "source_version_id": "srcv_test",
        "deep_revision": 1,
        "story_overview": {"protagonist": "林舟"},
        "characters": [{
            "id": "entity_protagonist",
            "name": "林舟",
            "role": "PROTAGONIST",
            "goals": ["找到寄信人"],
            "motivations": ["弄清密信来意"],
            "abilities": ["观察线索"],
            "event_ids": ["event_1", "event_2"],
            "evidence_ids": ["evidence_1", "evidence_2"],
        }],
        "events": [
            {
                "id": "event_1",
                "title": "发现密信",
                "people": ["林舟"],
                "chapter_ordinals": [1],
                "process": FIELD_GROUNDING_TEXT,
                "outcome": "林舟决定主动追查。",
                "evidence_ids": ["evidence_1"],
            },
            {
                "id": "event_2",
                "title": "追查线索",
                "event_type": "DECISION",
                "people": ["林舟"],
                "chapter_ordinals": [2],
                "start_char": 200,
                "end_char": 220,
                "process": "林舟核对新的线索。",
                "outcome": "追查方向发生变化。",
                "evidence_ids": ["evidence_2"],
            },
        ],
        "phases": [{
            "id": "phase_1",
            "title": "开始追查",
            "chapter_ordinals": [1, 2],
            "event_ids": ["event_1", "event_2"],
            "evidence_ids": ["evidence_1", "evidence_2"],
        }],
    }


def _output_dict() -> dict:
    field_values = {
        "surface_desire": "尽快找到寄信人。",
        "deep_desire": "想掌握自己的处境。",
        "motivation": "弄清密信来意。",
        "contrast": "警惕未知风险但确认线索后主动追查。",
        "boundary": "没有证据时不把猜测当成事实。",
        "core_ability": "从零散线索中确定下一步行动。",
    }
    return {
        "protagonist": "林舟",
        "fields": [
            {
                "field": field,
                "status": "SUPPORTED",
                "value": field_values[field],
                "first_display_chapter_ordinal": 1,
                "first_display_event_id": "event_1",
                "display_event": "林舟发现密信后决定主动追查。",
                "evidence_ids": ["evidence_1"],
                "explanation": "这一行动直接展示了人物要素。",
            }
            for field in CHARACTER_DESIGN_FIELDS
        ],
        "desire_conflicts": [{
            "chapter_ordinal": 2,
            "event_id": "event_2",
            "surface_desire_stage": "INITIAL",
            "surface_desire": "尽快找到寄信人。",
            "deep_desire": "想掌握自己的处境。",
            "motive": "继续追查",
            "motive_source": "PROTAGONIST",
            "motive_role": "PROTAGONIST_MOTIVE",
            "choice": "继续追查",
            "choice_polarity": "ACT",
            "result": "追查方向发生变化。",
            "sacrificed_desire": "DEEP",
            "sacrifice": "放弃被动等待",
            "sacrifice_role": "CONCRETE_COST",
            "arc_change": "主角从被动等待转向主动承担。",
            "motive_evidence_ids": ["evidence_2"],
            "choice_evidence_ids": ["evidence_2"],
            "result_evidence_ids": ["evidence_2_support"],
            "sacrifice_evidence_ids": ["evidence_2_support"],
        }],
        "arc_summary": "林舟由被动发现线索转为主动追查。",
    }


@pytest.mark.parametrize("mode", ["missing", "duplicate"])
def test_parser_rejects_missing_or_duplicate_contract_fields(mode: str) -> None:
    payload = _output_dict()
    if mode == "missing":
        payload["fields"].pop()
    else:
        payload["fields"][-1]["field"] = payload["fields"][0]["field"]

    with pytest.raises(CharacterDesignValidationError):
        parse_character_design_evidence(payload)


def test_parser_unwraps_schema_properties_envelope() -> None:
    output = parse_character_design_evidence({"properties": _output_dict()})

    assert output.protagonist == "林舟"
    assert len(output.fields) == 6


def test_program_compiles_zero_conflict_arc_without_model_embellishment() -> None:
    payload = _output_dict()
    payload["desire_conflicts"] = []
    payload["arc_summary"] = "主角屡次献祭生命，最终成为孤独的英雄。"
    output = parse_character_design_evidence(payload)

    summary = _compiled_arc_summary(output, covered_event_count=163)

    assert "尚未记录到有原文依据的欲望变化" in summary
    assert "不表示主角后续不会变化" in summary
    assert "覆盖全书 163 个主角事件" in summary
    assert "屡次献祭" not in summary
    assert "孤独的英雄" not in summary


def test_program_compiles_field_explanation_without_model_overclaim() -> None:
    output = parse_character_design_evidence(_output_dict())
    boundary = next(
        item for item in output.fields
        if item.field == "boundary"
    )
    boundary.explanation = "主角已经付出生命并获得后续绝对力量。"

    explanation = _compiled_field_explanation(boundary)

    assert "已经付出生命" not in explanation
    assert "后续绝对力量" not in explanation
    assert "尚未发生的代价" in explanation


def test_only_explicit_reversal_actions_receive_contrast_context() -> None:
    assert _needs_contrast_context({
        "event_type": "ACTION",
        "summary": "主角在同伴遇险后突然反击。",
    })
    assert not _needs_contrast_context({
        "event_type": "ACTION",
        "summary": "主角却继续向前调查。",
    })
    assert not _needs_contrast_context({
        "event_type": "DECISION",
        "summary": "主角在同伴遇险后突然反击。",
    })


def test_same_named_target_commitment_blocks_a_later_surface_first() -> None:
    earlier_event = {
        "title": "主角决定向苏清表白",
        "summary": "主角不再犹豫。",
        "process": "他准备在毕业前向苏清表白。",
        "outcome": "他决定放手一搏。",
    }

    assert _contains_earlier_same_named_target_commitment(
        "要是苏清接受他的表白，他就留下来。",
        earlier_event,
        other_character_names={"苏清"},
    )
    assert not _contains_earlier_same_named_target_commitment(
        "他想留在这座城市。",
        earlier_event,
        other_character_names={"苏清"},
    )


def test_reference_validation_does_not_require_subjective_field_keywords() -> None:
    payload = _output_dict()
    exact_quote = "林舟发现写着自己名字的密信后"
    for field in payload["fields"]:
        field["value"] = exact_quote
    payload["desire_conflicts"] = []
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        _projection(),
        {"evidence_1"},
        {"event_1": ["evidence_1"]},
        {"evidence_1": 1},
        {"evidence_1": FIELD_GROUNDING_TEXT},
    )

    assert [item.field for item in output.fields] == list(
        CHARACTER_DESIGN_FIELDS
    )


def test_reconciliation_keeps_five_fields_and_repairs_only_the_bad_field(
    monkeypatch,
) -> None:
    projection = _projection()
    projection.update({
        "narrative_status": "READY",
        "deep_status": "READY",
    })
    fingerprint = character_design_source_fingerprint(projection)
    run = SimpleNamespace(id="run_1")
    version = SimpleNamespace(id="srcv_test", total_chars=10_000)
    units = [
        SimpleNamespace(id="unit_1", title="第一章"),
        SimpleNamespace(id="unit_2", title="第二章"),
    ]
    evidence = SimpleNamespace(
        id="evidence_1",
        source_unit_id="unit_1",
        text_snapshot=FIELD_GROUNDING_TEXT,
        start_char=10,
    )

    class FakeSession:
        def get(self, model, object_id):
            if model.__name__ == "AnalysisRun" and object_id == run.id:
                return run
            if model.__name__ == "SourceVersion" and object_id == version.id:
                return version
            return None

    monkeypatch.setattr(
        character_design_service,
        "_base_projection",
        lambda _session, _run_id: projection,
    )
    monkeypatch.setattr(
        character_design_service,
        "_reference_validation_context",
        lambda _session, _version, _projection: (
            {"evidence_1"},
            {"event_1": ["evidence_1"], "event_2": []},
            {"evidence_1": 1},
            {"evidence_1": FIELD_GROUNDING_TEXT},
            2,
        ),
    )
    monkeypatch.setattr(
        character_design_service,
        "_chapter_catalog",
        lambda _session, _version_id: (
            units,
            {
                "unit_1": {"ordinal": 1, "title": "第一章"},
                "unit_2": {"ordinal": 2, "title": "第二章"},
            },
        ),
    )
    monkeypatch.setattr(
        character_design_service,
        "_event_support_evidence",
        lambda _session, _version_id, _events: (
            {"event_1": ["evidence_1"], "event_2": []},
            {"evidence_1": evidence},
            [],
        ),
    )
    monkeypatch.setattr(
        character_design_service,
        "resolve_analysis_profile",
        lambda _settings, _profile_id: (
            SimpleNamespace(),
            SimpleNamespace(
                max_output_tokens=16_000,
                context_window_tokens=1_000_000,
            ),
        ),
    )
    task_payload = {
        "run_id": run.id,
        "source_version_id": version.id,
        "source_fingerprint": fingerprint,
        "model_profile_id": "profile",
    }
    exact_quote = "林舟发现写着自己名字的密信后"
    fields = []
    for field_name in CHARACTER_DESIGN_FIELDS:
        fields.append({
            "field": field_name,
            "status": "SUPPORTED",
            "value": exact_quote,
            "first_display_chapter_ordinal": 1,
            "first_display_event_id": "event_1",
            "display_event": "发现密信",
            "evidence_ids": (
                ["missing_evidence"]
                if field_name == "core_ability"
                else ["evidence_1"]
            ),
            "explanation": "该原文支撑当前字段判断。",
        })
    first = reconcile_character_design_response(
        FakeSession(),
        task_payload=task_payload,
        value={
            "protagonist": "林舟",
            "fields": fields,
            "desire_conflicts": [],
            "arc_summary": "模型暂定总结。",
        },
        attempt_id="attempt_1",
    )

    assert first.output is None
    assert len(first.accepted_fields) == 5
    assert first.repair_components == ["core_ability"]
    repair_payload = {
        **task_payload,
        "accepted_character_fields": first.accepted_fields,
        "accepted_character_field_attempt_ids": (
            first.accepted_field_attempt_ids
        ),
        "pending_desire_conflicts": first.pending_desire_conflicts,
        "pending_desire_conflicts_attempt_id": (
            first.pending_desire_conflicts_attempt_id
        ),
        "repair_character_components": first.repair_components,
    }
    provider_payload = provider_payload_for_character_design(
        FakeSession(),
        SimpleNamespace(),
        repair_payload,
    )
    repair_input = json.loads(provider_payload["input"])
    assert repair_input["repair_request"]["components"] == [
        "core_ability"
    ]
    assert len(repair_input["repair_request"]["accepted_fields"]) == 5
    assert set(
        provider_payload["output_schema"]["properties"]
    ) == {"protagonist", "fields"}

    fixed_core_ability = {
        **fields[-1],
        "evidence_ids": ["evidence_1"],
    }
    second = reconcile_character_design_response(
        FakeSession(),
        task_payload=repair_payload,
        value={
            "protagonist": "林舟",
            "fields": [fixed_core_ability],
        },
        attempt_id="attempt_2",
    )

    assert second.output is not None
    assert len(second.accepted_fields) == 6
    assert second.repair_components == []
    assert second.accepted_desire_conflicts_attempt_id == "attempt_1"
    assert second.accepted_field_attempt_ids["surface_desire"] == "attempt_1"
    assert second.accepted_field_attempt_ids["core_ability"] == "attempt_2"

    # The separate conflict-only contract remains available when the
    # deferred conflict list itself fails objective validation.
    conflict_payload = {
        **task_payload,
        "accepted_character_fields": second.accepted_fields,
        "accepted_character_field_attempt_ids": (
            second.accepted_field_attempt_ids
        ),
        "repair_character_components": ["desire_conflicts"],
    }
    conflict_provider_payload = provider_payload_for_character_design(
        FakeSession(),
        SimpleNamespace(),
        conflict_payload,
    )
    assert set(
        conflict_provider_payload["output_schema"]["properties"]
    ) == {"protagonist", "desire_conflicts"}

    third = reconcile_character_design_response(
        FakeSession(),
        task_payload=conflict_payload,
        value={"protagonist": "林舟", "desire_conflicts": []},
        attempt_id="attempt_3",
    )

    assert third.output is not None
    assert len(third.output.fields) == 6
    assert third.accepted_desire_conflicts_attempt_id == "attempt_3"


def test_evolved_desire_allows_terminal_punctuation_outside_quotes() -> None:
    payload = _output_dict()
    payload["desire_conflicts"][0].update({
        "surface_desire_stage": "EVOLVED",
        "surface_desire": "主动追查新线索。",
        "arc_change": (
            "表层欲望由“尽快找到寄信人”演变为“主动追查新线索”。"
        ),
    })

    output = parse_character_design_evidence(payload)

    assert output.desire_conflicts[0].surface_desire == "主动追查新线索。"


def test_insufficient_field_cannot_invent_event_chapter_or_evidence() -> None:
    payload = _output_dict()
    payload["fields"][0].update({
        "status": "INSUFFICIENT_EVIDENCE",
        "value": "",
        "display_event": "",
    })

    with pytest.raises(CharacterDesignValidationError):
        parse_character_design_evidence(payload)


def test_insufficient_field_cannot_invent_display_event_text() -> None:
    payload = _output_dict()
    payload["fields"][0].update({
        "status": "INSUFFICIENT_EVIDENCE",
        "value": "",
        "first_display_chapter_ordinal": None,
        "first_display_event_id": None,
        "display_event": "林舟在没有可引用事件时仍表现出这一特征。",
        "evidence_ids": [],
    })

    with pytest.raises(CharacterDesignValidationError):
        parse_character_design_evidence(payload)


@pytest.mark.parametrize(
    ("update", "expected_code"),
    [
        ({"first_display_event_id": "event_missing"}, "CHARACTER_DESIGN_EVENT_REFERENCE_INVALID"),
        ({"first_display_chapter_ordinal": 2}, "CHARACTER_DESIGN_CHAPTER_REFERENCE_INVALID"),
        ({"evidence_ids": ["evidence_2"]}, "CHARACTER_DESIGN_EVENT_EVIDENCE_MISMATCH"),
    ],
)
def test_reference_validation_rejects_wrong_event_chapter_and_cross_event_evidence(
    update: dict,
    expected_code: str,
) -> None:
    payload = _output_dict()
    payload["fields"][0].update(update)
    output = parse_character_design_evidence(payload)

    with pytest.raises(ValueError, match=expected_code):
        _raise_for_references(
            output,
            _projection(),
            {"evidence_1", "evidence_2", "evidence_2_support"},
            {
                "event_1": ["evidence_1"],
                "event_2": ["evidence_2", "evidence_2_support"],
            },
            {
                "evidence_1": 1,
                "evidence_2": 2,
                "evidence_2_support": 2,
            },
            {
                "evidence_1": FIELD_GROUNDING_TEXT,
                "evidence_2": "林舟只是核对了旧线索。",
                "evidence_2_support": "追查方向发生变化。",
            },
        )


def test_observation_allows_one_quote_to_support_multiple_aspects() -> None:
    payload = _output_dict()
    payload["desire_conflicts"][0].update({
        "motive_evidence_ids": ["evidence_2"],
        "choice_evidence_ids": ["evidence_2"],
        "result_evidence_ids": ["evidence_2"],
        "sacrifice_evidence_ids": ["evidence_2"],
    })

    output = parse_character_design_evidence(payload)
    assert len(output.desire_conflicts) == 1


def test_program_keeps_possible_cost_as_a_partial_observation() -> None:
    payload = _output_dict()
    payload["desire_conflicts"][0].update({
        "sacrifice": "这件事也许会有风险。",
        "sacrifice_role": "POSSIBLE_COST",
    })
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        _projection(),
        {"evidence_1", "evidence_2", "evidence_2_support"},
        {
            "event_1": ["evidence_1"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 1,
            "evidence_2": 2,
            "evidence_2_support": 2,
        },
        {
            "evidence_1": FIELD_GROUNDING_TEXT,
            "evidence_2": "林舟决定继续追查。",
            "evidence_2_support": "这件事也许会有风险，追查方向发生变化。",
        },
    )

    assert len(output.desire_conflicts) == 1
    assert output.desire_conflicts[0].sacrifice_role == "POSSIBLE_COST"


def test_program_does_not_override_model_cost_classification() -> None:
    payload = _output_dict()
    payload["desire_conflicts"][0]["sacrifice_role"] = "POSSIBLE_COST"
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        _projection(),
        {"evidence_1", "evidence_2", "evidence_2_support"},
        {
            "event_1": ["evidence_1"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 1,
            "evidence_2": 2,
            "evidence_2_support": 2,
        },
        {
            "evidence_1": FIELD_GROUNDING_TEXT,
            "evidence_2": "林舟决定继续追查。",
            "evidence_2_support": "放弃被动等待后，追查方向发生变化。",
        },
    )

    assert output.desire_conflicts[0].sacrifice_role == "POSSIBLE_COST"


def test_decision_support_keeps_short_nearby_choice_and_outcome_quotes() -> None:
    evidence = [
        SimpleNamespace(
            id=f"noise_{index:02d}",
            start_char=1_000 + index * 20,
            end_char=1_019 + index * 20,
            text_snapshot="路明非面对危机继续行动并承受结果。",
        )
        for index in range(40)
    ]
    evidence.extend([
        SimpleNamespace(
            id="exchange",
            start_char=950,
            end_char=955,
            text_snapshot="交换。",
        ),
        SimpleNamespace(
            id="motive",
            start_char=970,
            end_char=999,
            text_snapshot="不想她死，也不想总是一个人。",
        ),
        SimpleNamespace(
            id="revoke",
            start_char=1_021,
            end_char=1_035,
            text_snapshot="他伸手对龙王说：撤销。",
        ),
        SimpleNamespace(
            id="outcome",
            start_char=1_040,
            end_char=1_070,
            text_snapshot="湿婆业舞被强行中断了。",
        ),
    ])

    ranked = _rank_decision_support(
        evidence,
        event_start=1_000,
        event_end=1_020,
        query_ngrams=_evidence_overlap_ngrams(
            "交换生命拯救同伴，撤销龙王的湿婆业舞"
        ),
        proximity_limit=21,
    )

    selected = {item.id for item in ranked[:22]}
    assert {"exchange", "motive", "revoke", "outcome"}.issubset(selected)


def test_event_support_never_drops_original_anchors() -> None:
    original = [f"original_{index}" for index in range(25)]

    selected = _merge_event_support_ids(
        original,
        ["expanded_1", "expanded_2"],
    )

    assert selected == original


def test_first_display_guardrail_exposes_earlier_repeated_action() -> None:
    events = [
        {
            "id": "event_early",
            "title": "首次输入地图秘籍",
            "chapter_ordinals": [5],
            "start_char": 100,
            "process": "主角输入 Black Sheep Wall 打开全图。",
            "evidence_ids": ["early_result"],
        },
        {
            "id": "event_late",
            "title": "再次输入地图秘籍",
            "chapter_ordinals": [10],
            "start_char": 200,
            "process": "主角再次输入 Black Sheep Wall 联系外界。",
            "evidence_ids": ["late_action"],
        },
    ]
    evidence = {
        "early_result": SimpleNamespace(
            id="early_result",
            text_snapshot="完整地图出现在屏幕上。",
        ),
        "late_action": SimpleNamespace(
            id="late_action",
            text_snapshot="Black Sheep Wall！",
        ),
    }

    guardrails = _first_display_repeat_guardrails(events, evidence)

    assert guardrails == [{
        "earlier_event_id": "event_early",
        "earlier_chapter_ordinal": 5,
        "later_event_id": "event_late",
        "later_chapter_ordinal": 10,
        "repeated_evidence_id": "late_action",
        "repeated_quote": "Black Sheep Wall！",
    }]


def test_small_context_budget_never_exceeds_available_input() -> None:
    profile = SimpleNamespace(
        max_output_tokens=30_000,
        context_window_tokens=50_000,
    )

    budget_chars = _request_budget_chars(profile)

    assert budget_chars == int(
        (
            min(
                character_design_service.CHARACTER_DESIGN_SOFT_INPUT_TOKENS,
                50_000 - 30_000,
            )
            - character_design_service.CHARACTER_DESIGN_REQUEST_OVERHEAD_TOKENS
        )
        * character_design_service.CHARACTER_DESIGN_ESTIMATED_CHARS_PER_TOKEN
    )


def test_large_context_budget_reserves_request_overhead_below_soft_cap() -> None:
    profile = SimpleNamespace(
        max_output_tokens=65_536,
        context_window_tokens=1_048_576,
    )

    budget_chars = _request_budget_chars(profile)

    assert budget_chars == int(
        (
            character_design_service.CHARACTER_DESIGN_SOFT_INPUT_TOKENS
            - character_design_service.CHARACTER_DESIGN_REQUEST_OVERHEAD_TOKENS
        )
        * character_design_service.CHARACTER_DESIGN_ESTIMATED_CHARS_PER_TOKEN
    )


def test_support_parameters_are_part_of_source_fingerprint(
    monkeypatch,
) -> None:
    projection = _projection()
    original = character_design_source_fingerprint(projection)
    monkeypatch.setattr(
        character_design_service,
        "CHARACTER_DESIGN_EVENT_SUPPORT_LIMIT",
        23,
    )

    assert character_design_source_fingerprint(projection) != original


def test_reference_validation_rejects_evidence_from_another_event_chapter() -> None:
    payload = _output_dict()
    output = parse_character_design_evidence(payload)

    with pytest.raises(
        ValueError,
        match="CHARACTER_DESIGN_EVIDENCE_CHAPTER_MISMATCH",
    ):
        _raise_for_references(
            output,
            _projection(),
            {"evidence_1", "evidence_2", "evidence_2_support"},
            {
                "event_1": ["evidence_1"],
                "event_2": ["evidence_2", "evidence_2_support"],
            },
            {
                "evidence_1": 1,
                "evidence_2": 2,
                "evidence_2_support": 3,
            },
        )


def test_conflict_requires_explicit_event_result_evidence() -> None:
    payload = _output_dict()
    payload["desire_conflicts"][0].update({
        "motive": "核对了旧线索",
        "choice": "追查方向发生变化",
        "sacrifice": "放弃等待",
    })
    payload["desire_conflicts"][0]["choice_evidence_ids"] = [
        "evidence_2_support"
    ]
    payload["desire_conflicts"][0]["result_evidence_ids"] = ["evidence_2"]
    output = parse_character_design_evidence(payload)

    with pytest.raises(
        ValueError,
        match="CHARACTER_DESIGN_OBSERVATION_RESULT_EVIDENCE_INCOMPLETE",
    ):
        _raise_for_references(
            output,
            _projection(),
            {"evidence_1", "evidence_2", "evidence_2_support"},
            {
                "event_1": ["evidence_1"],
                "event_2": ["evidence_2", "evidence_2_support"],
            },
            {
                "evidence_1": 1,
                "evidence_2": 2,
                "evidence_2_support": 2,
            },
            {
                "evidence_1": FIELD_GROUNDING_TEXT,
                "evidence_2": "林舟只是核对了旧线索。",
                "evidence_2_support": "放弃等待后，追查方向发生变化。",
            },
        )


def test_result_claim_must_match_event_and_selected_quote() -> None:
    claim = "龙王的湿婆业舞被强行中断。"

    assert _claim_is_grounded(
        claim,
        ["龙王自己也无法停止的湿婆业舞被强行中断了！"],
    )
    assert not _claim_is_grounded(
        claim,
        ["路明非缓缓苏醒，拔出钢筋后伤口痊愈。"],
    )
    assert not _claim_is_grounded(
        "湿婆业舞没有被中断。",
        ["龙王自己也无法停止的湿婆业舞被强行中断了！"],
    )


def test_short_exact_action_quote_is_grounded_but_character_name_is_not() -> None:
    assert _claim_is_grounded("选择交换", ["交换。"])
    assert not _claim_is_grounded(
        "路明非选择交换",
        ["路明非闭眼睡去。"],
        ignored_terms=("路明非",),
    )


def test_exact_quote_is_not_rejected_by_later_negation_in_same_evidence() -> None:
    claim = (
        "可他也怕自己会坚持不住把手收回来，收回来，诺诺就死了。"
        "他希望快点完成这个交易，把后路给断了"
    )
    evidence = (
        "路明非伸出手，死死咬着牙。可他也怕自己会坚持不住把手收回来，"
        "收回来，诺诺就死了。他希望快点完成这个交易，把后路给断了，"
        "没了后路也就不用怕什么了。"
    )

    assert _claim_is_strictly_grounded(
        claim,
        [evidence],
        ignored_terms=("路明非",),
    )


def test_short_exchange_quote_is_kept_without_program_cost_judgment() -> None:
    payload = _output_dict()
    conflict = payload["desire_conflicts"][0]
    conflict.update({
        "motive": "继续追查",
        "choice": "交换",
        "choice_polarity": "ACCEPT",
        "sacrifice": "交换",
        "choice_evidence_ids": ["evidence_2"],
        "sacrifice_evidence_ids": ["evidence_2"],
    })
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        _projection(),
        {"evidence_1", "evidence_2", "evidence_2_support"},
        {
            "event_1": ["evidence_1"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 1,
            "evidence_2": 2,
            "evidence_2_support": 2,
        },
        {
            "evidence_1": FIELD_GROUNDING_TEXT,
            "evidence_2": "林舟决定继续追查：交换。",
            "evidence_2_support": "放弃等待后，追查方向发生变化。",
        },
    )
    assert output.desire_conflicts[0].sacrifice == "交换"


def test_conflict_accepts_a_direct_concrete_cost_quote() -> None:
    payload = _output_dict()
    conflict = payload["desire_conflicts"][0]
    conflict.update({
        "motive": "继续追查",
        "choice": "交换",
        "choice_polarity": "ACCEPT",
        "sacrifice": "放弃等待",
        "choice_evidence_ids": ["evidence_2"],
        "sacrifice_evidence_ids": ["evidence_2_support"],
    })
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        _projection(),
        {"evidence_1", "evidence_2", "evidence_2_support"},
        {
            "event_1": ["evidence_1"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 1,
            "evidence_2": 2,
            "evidence_2_support": 2,
        },
        {
            "evidence_1": FIELD_GROUNDING_TEXT,
            "evidence_2": "林舟决定继续追查：交换。",
            "evidence_2_support": "放弃等待后，追查方向发生变化。",
        },
    )


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        (
            "motive",
            "统治世界",
            "CHARACTER_DESIGN_OBSERVATION_MOTIVE_EVIDENCE_INCOMPLETE",
        ),
        (
            "choice",
            "抛下同伴独自逃走",
            "CHARACTER_DESIGN_OBSERVATION_CHOICE_EVIDENCE_INCOMPLETE",
        ),
        (
            "sacrifice",
            "失去并不存在的王位",
            "CHARACTER_DESIGN_OBSERVATION_SACRIFICE_EVIDENCE_INCOMPLETE",
        ),
    ],
)
def test_conflict_roles_must_quote_their_own_selected_evidence(
    field: str,
    value: str,
    expected_code: str,
) -> None:
    payload = _output_dict()
    conflict = payload["desire_conflicts"][0]
    conflict.update({
        "motive": "继续追查",
        "choice": "交换",
        "choice_polarity": "ACCEPT",
        "sacrifice": "放弃等待",
        "choice_evidence_ids": ["evidence_2"],
        "sacrifice_evidence_ids": ["evidence_2_support"],
        field: value,
    })
    output = parse_character_design_evidence(payload)

    with pytest.raises(ValueError, match=expected_code):
        _raise_for_references(
            output,
            _projection(),
            {"evidence_1", "evidence_2", "evidence_2_support"},
            {
                "event_1": ["evidence_1"],
                "event_2": ["evidence_2", "evidence_2_support"],
            },
            {
                "evidence_1": 1,
                "evidence_2": 2,
                "evidence_2_support": 2,
            },
            {
                "evidence_1": FIELD_GROUNDING_TEXT,
                "evidence_2": "林舟决定继续追查：交换。",
                "evidence_2_support": "放弃等待后，追查方向发生变化。",
            },
        )


def test_partial_observation_fields_are_optional() -> None:
    payload = _output_dict()
    conflict = payload["desire_conflicts"][0]
    conflict.update({
        "observation_kind": "PRESSURE",
        "surface_desire": "",
        "deep_desire": "",
        "motive": "继续追查",
        "choice": "",
        "result": "",
        "sacrifice": "",
        "choice_evidence_ids": [],
        "result_evidence_ids": [],
        "sacrifice_evidence_ids": [],
        "arc_change": "追查目标受到外部压力，但是否转向尚不能定论。",
    })
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        _projection(),
        {"evidence_1", "evidence_2", "evidence_2_support"},
        {
            "event_1": ["evidence_1"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 1,
            "evidence_2": 2,
            "evidence_2_support": 2,
        },
        {
            "evidence_1": FIELD_GROUNDING_TEXT,
            "evidence_2": "林舟决定继续追查。",
            "evidence_2_support": "追查方向发生变化。",
        },
    )
    assert output.desire_conflicts[0].observation_kind == "PRESSURE"


def test_external_offer_can_be_recorded_as_pressure_if_evidence_backed() -> None:
    payload = _output_dict()
    conflict = payload["desire_conflicts"][0]
    conflict.update({
        "observation_kind": "PRESSURE",
        "motive": "提供一个加入学院的机会",
        "motive_source": "OTHER_CHARACTER",
        "motive_role": "EXTERNAL_OFFER",
        "choice": "",
        "result": "",
        "sacrifice": "",
        "choice_evidence_ids": [],
        "result_evidence_ids": [],
        "sacrifice_evidence_ids": [],
    })
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        _projection(),
        {"evidence_1", "evidence_2", "evidence_2_support"},
        {
            "event_1": ["evidence_1"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 1,
            "evidence_2": 2,
            "evidence_2_support": 2,
        },
        {
            "evidence_1": FIELD_GROUNDING_TEXT,
            "evidence_2": (
                "同伴提供一个加入学院的机会，林舟决定继续追查。"
            ),
            "evidence_2_support": "放弃等待后，追查方向发生变化。",
        },
    )
    assert output.desire_conflicts[0].motive_role == "EXTERNAL_OFFER"


def test_program_does_not_override_choice_or_cost_labels() -> None:
    payload = _output_dict()
    conflict = payload["desire_conflicts"][0]
    conflict.update({
        "choice": "我不想跟你换",
        "choice_polarity": "ACCEPT",
        "sacrifice": "我不想失去生命",
        "sacrifice_role": "CONCRETE_COST",
        "choice_evidence_ids": ["evidence_2"],
        "sacrifice_evidence_ids": ["evidence_2_support"],
    })
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        _projection(),
        {"evidence_1", "evidence_2", "evidence_2_support"},
        {
            "event_1": ["evidence_1"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 1,
            "evidence_2": 2,
            "evidence_2_support": 2,
        },
        {
            "evidence_1": FIELD_GROUNDING_TEXT,
            "evidence_2": "林舟决定继续追查。我不想跟你换。",
            "evidence_2_support": (
                "我不想失去生命。追查方向发生变化。"
            ),
        },
    )
    assert output.desire_conflicts[0].choice_polarity == "ACCEPT"
    assert output.desire_conflicts[0].sacrifice_role == "CONCRETE_COST"


def test_supported_field_value_must_match_selected_event_and_evidence() -> None:
    payload = _output_dict()
    payload["fields"][0].update({
        "value": "抛弃所有同伴独自称王。",
        "display_event": "原文中不存在的称王决定。",
    })
    payload["desire_conflicts"] = []
    output = parse_character_design_evidence(payload)

    with pytest.raises(
        ValueError,
        match=(
            "CHARACTER_DESIGN_FIELD_"
            "(?:DISPLAY_EVENT|VALUE)_(?:UNGROUNDED|NOT_CONTIGUOUS)"
        ),
    ):
        _raise_for_references(
            output,
            _projection(),
            {"evidence_1", "evidence_2", "evidence_2_support"},
            {
                "event_1": ["evidence_1"],
                "event_2": ["evidence_2", "evidence_2_support"],
            },
            {
                "evidence_1": 1,
                "evidence_2": 2,
                "evidence_2_support": 2,
            },
            {
                "evidence_1": FIELD_GROUNDING_TEXT,
                "evidence_2": "林舟决定继续追查。",
                "evidence_2_support": "放弃等待后，追查方向发生变化。",
            },
        )


def test_program_replaces_model_display_event_with_selected_event_title() -> None:
    payload = _output_dict()
    payload["fields"][0]["display_event"] = "模型自由改写且没有原文依据。"
    payload["desire_conflicts"] = []
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        _projection(),
        {"evidence_1", "evidence_2", "evidence_2_support"},
        {
            "event_1": ["evidence_1"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 1,
            "evidence_2": 2,
            "evidence_2_support": 2,
        },
        {
            "evidence_1": FIELD_GROUNDING_TEXT,
            "evidence_2": "林舟决定继续追查。",
            "evidence_2_support": "放弃等待后，追查方向发生变化。",
        },
    )

    assert output.fields[0].display_event == "发现密信"


def test_supported_field_rejects_a_grounded_prefix_with_hallucinated_tail() -> None:
    payload = _output_dict()
    payload["fields"][0]["value"] = "尽快找到寄信人并独自称王。"
    payload["desire_conflicts"] = []
    output = parse_character_design_evidence(payload)

    with pytest.raises(
        ValueError,
        match="CHARACTER_DESIGN_FIELD_VALUE_UNGROUNDED",
    ):
        _raise_for_references(
            output,
            _projection(),
            {"evidence_1", "evidence_2", "evidence_2_support"},
            {
                "event_1": ["evidence_1"],
                "event_2": ["evidence_2", "evidence_2_support"],
            },
            {
                "evidence_1": 1,
                "evidence_2": 2,
                "evidence_2_support": 2,
            },
            {
                "evidence_1": FIELD_GROUNDING_TEXT,
                "evidence_2": "林舟决定继续追查。",
                "evidence_2_support": (
                    "放弃等待后，追查方向发生变化。"
                ),
            },
        )


def test_generic_semantic_overlap_does_not_hard_reject_first_display() -> None:
    projection = _projection()
    projection["characters"][0]["event_ids"] = ["event_1", "event_2"]
    projection["events"][0].update({
        "event_type": "DECISION",
        "chapter_ordinals": [11],
        "process": (
            f"{FIELD_GROUNDING_TEXT} "
            "主角害怕收手后同伴会死，也害怕失去在意的人。"
        ),
    })
    projection["events"][1].update({
        "event_type": "DECISION",
        "chapter_ordinals": [32],
        "process": "主角说不想她死，也不想失去这些羁绊。",
        "outcome": "主角决定回来保护同伴。",
    })
    payload = _output_dict()
    for field in payload["fields"]:
        field["first_display_chapter_ordinal"] = 11
    motivation = next(
        field
        for field in payload["fields"]
        if field["field"] == "motivation"
    )
    motivation.update({
        "value": "不想她死，也不想失去这些羁绊。",
        "first_display_chapter_ordinal": 32,
        "first_display_event_id": "event_2",
        "display_event": "主角不想她死，决定回来保护同伴。",
        "evidence_ids": ["evidence_2"],
    })
    payload["desire_conflicts"] = []
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        projection,
        {"evidence_1", "evidence_2", "evidence_2_support"},
        {
            "event_1": ["evidence_1"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 11,
            "evidence_2": 32,
            "evidence_2_support": 32,
        },
        {
            "evidence_1": (
                f"{FIELD_GROUNDING_TEXT} "
                "他害怕收手后同伴会死，也害怕失去在意的人。"
            ),
            "evidence_2": (
                "他不想她死，也不想失去这些羁绊，"
                "决定回来保护同伴。"
            ),
            "evidence_2_support": "主角回来后保护了同伴。",
        },
    )


def test_semantic_first_display_ignores_unowned_earlier_support() -> None:
    projection = _projection()
    projection["events"][0].update({
        "event_type": "DECISION",
        "chapter_ordinals": [11],
        "process": "林舟接受了契约。",
        "outcome": "契约生效。",
    })
    projection["events"][1].update({
        "event_type": "DECISION",
        "chapter_ordinals": [21],
        "process": "林舟决定回来保护同伴。",
        "outcome": "他不想失去同伴。",
    })
    payload = _output_dict()
    for field in payload["fields"]:
        field["first_display_chapter_ordinal"] = 11
    deep_desire = next(
        field
        for field in payload["fields"]
        if field["field"] == "deep_desire"
    )
    deep_desire.update({
        "value": "他不想失去同伴，决定回来保护他们。",
        "first_display_chapter_ordinal": 21,
        "first_display_event_id": "event_2",
        "display_event": "林舟决定回来保护同伴。",
        "evidence_ids": ["evidence_2"],
    })
    payload["desire_conflicts"] = []
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        projection,
        {
            "evidence_1",
            "evidence_1_support",
            "evidence_2",
            "evidence_2_support",
        },
        {
            "event_1": ["evidence_1", "evidence_1_support"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 11,
            "evidence_1_support": 11,
            "evidence_2": 21,
            "evidence_2_support": 21,
        },
        {
            "evidence_1": FIELD_GROUNDING_TEXT,
            "evidence_1_support": (
                "他怕收手后同伴会死，也怕失去在意的人，"
                "决定付出生命救她。"
            ),
            "evidence_2": (
                "他不想失去同伴，决定回来保护他们。"
            ),
            "evidence_2_support": "林舟决定回来保护同伴。",
        },
    )


def test_first_display_repeat_ignores_unowned_earlier_support() -> None:
    projection = _projection()
    projection["events"][0].update({
        "event_type": "DECISION",
        "chapter_ordinals": [1],
        "process": "林舟接受了邀请。",
        "outcome": "邀请生效。",
    })
    projection["events"][1].update({
        "event_type": "DECISION",
        "chapter_ordinals": [2],
        "process": "林舟输入 Black Sheep Wall 打开全图。",
        "outcome": "地图完成刷新。",
    })
    payload = _output_dict()
    core_ability = next(
        item for item in payload["fields"]
        if item["field"] == "core_ability"
    )
    core_ability.update({
        "value": "输入 Black Sheep Wall 打开全图。",
        "first_display_chapter_ordinal": 2,
        "first_display_event_id": "event_2",
        "display_event": "林舟输入 Black Sheep Wall 打开全图。",
        "evidence_ids": ["evidence_2"],
    })
    payload["desire_conflicts"] = []
    payload["desire_conflicts"] = []
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        projection,
        {
            "evidence_1",
            "evidence_1_support",
            "evidence_2",
            "evidence_2_support",
        },
        {
            "event_1": ["evidence_1", "evidence_1_support"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 1,
            "evidence_1_support": 1,
            "evidence_2": 2,
            "evidence_2_support": 2,
        },
        {
            "evidence_1": FIELD_GROUNDING_TEXT,
            "evidence_1_support": (
                "林舟输入 Black Sheep Wall 打开全图。"
            ),
            "evidence_2": "林舟输入 Black Sheep Wall 打开全图。",
            "evidence_2_support": "地图完成刷新。",
        },
    )


def test_program_does_not_use_repeat_heuristic_as_literary_gate() -> None:
    projection = _projection()
    projection["events"][0]["process"] = (
        "林舟输入 Black Sheep Wall 后首次打开全图。"
    )
    projection["events"][1]["process"] = (
        "林舟再次输入 Black Sheep Wall 联系外界。"
    )
    payload = _output_dict()
    core_ability = next(
        item for item in payload["fields"]
        if item["field"] == "core_ability"
    )
    core_ability.update({
        "value": "输入 Black Sheep Wall 联系外界。",
        "first_display_chapter_ordinal": 2,
        "first_display_event_id": "event_2",
        "display_event": "林舟再次输入 Black Sheep Wall 联系外界。",
        "evidence_ids": ["evidence_2"],
    })
    payload["desire_conflicts"] = []
    output = parse_character_design_evidence(payload)

    _raise_for_references(
        output,
        projection,
        {"evidence_1", "evidence_2", "evidence_2_support"},
        {
            "event_1": ["evidence_1"],
            "event_2": ["evidence_2", "evidence_2_support"],
        },
        {
            "evidence_1": 1,
            "evidence_2": 2,
            "evidence_2_support": 2,
        },
        {
            "evidence_1": (
                f"{FIELD_GROUNDING_TEXT} "
                "林舟输入 Black Sheep Wall 打开全图。"
            ),
            "evidence_2": (
                "林舟再次输入 Black Sheep Wall 联系外界。"
            ),
            "evidence_2_support": "追查方向发生变化。",
        },
    )


def test_distinctive_later_repeat_is_not_accepted_as_first_display() -> None:
    assert _contains_earlier_distinctive_repeat(
        "Black Sheep Wall！",
        {
            "title": "解析青铜城地图",
            "process": "路明非输入 Black Sheep Wall 指令。",
        },
    )
    assert not _contains_earlier_distinctive_repeat(
        "路明非作出选择。",
        {
            "title": "另一场选择",
            "process": "路明非进入教室。",
        },
    )


def test_parser_allows_later_desire_reinterpretation() -> None:
    payload = _output_dict()
    payload["desire_conflicts"][0]["deep_desire"] = "接受别人提供的机会。"

    parsed = parse_character_design_evidence(payload)
    assert (
        parsed.desire_conflicts[0].deep_desire
        == "接受别人提供的机会。"
    )


def test_parser_does_not_hard_reject_model_arc_summary_wording() -> None:
    payload = _output_dict()
    payload["desire_conflicts"][0].update({
        "surface_desire_stage": "EVOLVED",
        "surface_desire": "保护已经接纳自己的同伴。",
        "arc_change": (
            "表层欲望从尽快找到寄信人。演化为"
            "保护已经接纳自己的同伴。"
        ),
    })
    payload["arc_summary"] = "林舟的表层欲望始终是尽快找到寄信人。"

    parsed = parse_character_design_evidence(payload)

    assert parsed.arc_summary == payload["arc_summary"]


def test_parser_does_not_hard_reject_subjective_arc_change_wording() -> None:
    payload = _output_dict()
    payload["desire_conflicts"][0][
        "arc_change"
    ] = "他的深层渴望不再是寻找归属，而是统治世界。"

    parsed = parse_character_design_evidence(payload)

    assert parsed.desire_conflicts[0].arc_change == payload[
        "desire_conflicts"
    ][0]["arc_change"]


@pytest.mark.parametrize(
    "arc_summary",
    (
        "主角的深层欲望不再是掌握自己的处境，而是统治世界。",
        "主角的深层欲望并非被替换，而是在新的关系中继续显现。",
        "主角的深层欲望并非被替换，而是变成统治世界。",
    ),
)
def test_parser_accepts_arc_summary_for_programmatic_replacement(
    arc_summary: str,
) -> None:
    payload = _output_dict()
    payload["arc_summary"] = arc_summary

    parsed = parse_character_design_evidence(payload)

    assert parsed.arc_summary == arc_summary


def test_source_fingerprint_changes_when_deep_revision_or_events_change() -> None:
    projection = _projection()
    original = character_design_source_fingerprint(projection)
    revised_deep = deepcopy(projection)
    revised_deep["deep_revision"] = 2
    revised_event = deepcopy(projection)
    revised_event["events"][0]["outcome"] = "林舟决定暂时隐瞒密信。"

    assert character_design_source_fingerprint(revised_deep) != original
    assert character_design_source_fingerprint(revised_event) != original


def test_unclassified_supporting_characters_do_not_block_protagonist_ledger() -> None:
    projection = _projection()
    projection.update({
        "narrative_status": "INCOMPLETE",
        "story_overview": {"protagonist": "林舟"},
    })

    assert _character_design_sources_ready(projection) is True
    projection["phases"] = []
    assert _character_design_sources_ready(projection) is False


def test_story_overview_name_wins_when_multiple_characters_are_marked_protagonist() -> None:
    projection = _projection()
    projection["story_overview"]["protagonist"] = "林舟"
    projection["characters"].insert(0, {
        "id": "entity_other_lead",
        "name": "顾川",
        "aliases": [],
        "role": "PROTAGONIST",
    })
    projection["characters"][1]["role"] = "PROTAGONIST"

    name, character = _protagonist(projection)

    assert name == "林舟"
    assert character is not None
    assert character["id"] == "entity_protagonist"
