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
from app.models import (
    AnalysisRun,
    AnalysisRunStatus,
    AnalysisRunTask,
    EntityCandidate,
    EventCandidate,
    NarrativeSynthesis,
    Task,
    TaskAttempt,
    TaskStatus,
)
from app.providers.base import ProviderError, ProviderResponse
from app.providers.openai_responses import OpenAIResponsesProvider
from app.providers.registry import ProviderRegistry
from app.repositories import claim_next_task, fail_task_attempt
from app.services import chapter_end_hooks as chapter_end_hooks_service
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
            "process": (
                "林舟看见桌上放着一封写有自己名字的密信。他想尽快找到寄信人，"
                "弄清来意并掌握自己的处境；虽然警惕未知风险，确认线索后仍决定"
                "主动追查，没有证据时不把猜测当成事实，并从密信线索中确定"
                "下一步行动。"
            ),
            "outcome": "林舟确认有人专门给自己留下了密信。",
            "impact": "林舟决定追查寄信人的身份和目的。",
            "discovery_routes": ["INFORMATION_CHANGE"],
            "evidence_quotes": [
                (
                    "桌上放着一封写着他名字的密信。林舟想尽快找到寄信人，"
                    "弄清来意并掌握自己的处境；虽然警惕未知风险，确认线索后"
                    "仍决定主动追查，没有证据时不把猜测当成事实，并从密信"
                    "线索中确定下一步行动。"
                )
            ],
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
            component = (foundation.get("requested_component") or {}).get("key")
            component_fields = {
                "overview": ("story_overview",),
                "characters": ("character_roles",),
                "plot": ("narrative_phases",),
                "relations": ("character_relations", "event_relations"),
            }
            if component:
                output = {key: output[key] for key in component_fields[component]}
        elif task_kind == "analysis.deep_insights":
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
                    },
                    {
                        "subject": "旧宅",
                        "predicate": "木门状态",
                        "value": "模型试图改写无关事实" if is_revision else "木门老旧",
                        "fact_type": "PLACE",
                        "status": "CONFIRMED",
                        "valid_from_chapter": 1,
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
                        "proposition": "桌上有一封写着自己名字的密信",
                        "state": "KNOWS",
                        "chapter_ordinal": 1,
                        "evidence_ids": [evidence_id],
                    }
                ],
                "knowledge_transfers": [
                    {
                        "source_actor": "直接观察",
                        "target_actor": character["name"],
                        "proposition": "桌上有一封写着自己名字的密信",
                        "transfer_type": "WITNESSED",
                        "resulting_state": "KNOWS",
                        "chapter_ordinal": 1,
                        "event_id": event["id"],
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
        elif task_kind == "analysis.character_design_evidence":
            character_input = json.loads(payload["input"])
            assert set(character_input["protagonist"]) == {
                "id",
                "name",
                "aliases",
            }
            assert "narrative_phases" not in character_input
            assert [
                event["sequence_no"]
                for event in character_input["protagonist_events"]
            ] == list(range(
                character_input["protagonist_events"][0]["sequence_no"],
                character_input["protagonist_events"][-1]["sequence_no"] + 1,
            ))
            if character_input.get("window", {}).get("phase") == "CONFLICTS":
                return ProviderResponse(
                    parsed={
                        "protagonist": character_input["protagonist"]["name"],
                        "desire_conflicts": [],
                    },
                    raw_text="{}",
                    prompt_tokens=120,
                    completion_tokens=80,
                    provider_id=self.name,
                    model="static-analysis",
                    parameters={},
                )
            event = character_input["protagonist_events"][0]
            evidence_id = event["evidence_ids"][0]
            chapter_ordinal = event["chapter_ordinals"][0]
            field_values = {
                "surface_desire": "尽快找到寄信人，弄清来意。",
                "deep_desire": (
                    "林舟想尽快找到寄信人，"
                    "弄清来意并掌握自己的处境。"
                ),
                "motivation": "林舟想尽快找到寄信人，弄清来意。",
                "contrast": (
                    "虽然警惕未知风险，"
                    "确认线索后仍决定主动追查"
                ),
                "boundary": "没有证据时不把猜测当成事实。",
                "core_ability": "从密信线索中确定下一步行动。",
            }
            output = {
                "protagonist": character_input["protagonist"]["name"],
                "fields": [
                    {
                        "field": field,
                        "status": "SUPPORTED",
                        "value": value,
                        "first_display_chapter_ordinal": chapter_ordinal,
                        "first_display_event_id": event["id"],
                        "display_event": "林舟发现写着自己名字的密信后，确认线索并决定主动追查。",
                        "evidence_ids": [evidence_id],
                        "explanation": "这一决定通过可核查行动展示了人物要素，而不是只依赖人物简介。",
                    }
                    for field, value in field_values.items()
                ],
                "desire_conflicts": [],
                "arc_summary": "林舟由被动回到旧宅，转为主动追查密信来源；短篇样本只能证明这一阶段变化。",
            }
        elif task_kind == "analysis.opening_payoff_candidates":
            payoff_input = json.loads(payload["input"])
            classifications = []
            for candidate in payoff_input["candidates"]:
                program_exclusion = (
                    candidate["program_exclusion_code"] or ""
                )
                is_complete = not program_exclusion
                classifications.append({
                    "sequence_no": candidate["sequence_no"],
                    "matched_facet_ids": (
                        ["F1", "F2", "F3"]
                        if is_complete
                        else []
                    ),
                    "exclusion_code": (
                        program_exclusion
                        or "NONE"
                    ),
                    "anchor_evidence_no": 1,
                })
                if is_complete:
                    break
            output = {
                "classifications": classifications,
            }
        elif task_kind == "analysis.chapter_end_hooks":
            hook_input = json.loads(payload["input"])
            assert "narrative_phases" not in hook_input
            output = {
                "chapters": [
                    {
                        "chapter_ordinal": ending["chapter_ordinal"],
                        "ending_evidence_id": ending["ending_evidence_id"],
                        "hook_type": "NEW_INFORMATION" if ending["chapter_ordinal"] == 1 else "NONE",
                        "strength": "STRONG" if ending["chapter_ordinal"] == 1 else "NONE",
                        "hook_question": "寄信人是谁？" if ending["chapter_ordinal"] == 1 else "",
                        "rationale": "密信在章末抛出新的身份问题。" if ending["chapter_ordinal"] == 1 else "主角作出决定后完整收束。",
                        "retention_basis": (
                            "章末留下寄信人的身份缺口。"
                            if ending["chapter_ordinal"] == 1
                            else "本章完成行动结算，没有新增未闭合问题。"
                        ),
                        "response_status": "NOT_APPLICABLE",
                        "response_evidence_id": None,
                        "response_summary": "",
                    }
                    for ending in hook_input["chapter_endings"]
                ]
            }
        else:
            assert task_kind == "analysis.learning_report"
            report_input = json.loads(payload["input"])
            evidence_id = next(
                evidence["id"]
                for material in report_input["materials"]
                for evidence in material["evidence"]
            )
            answers = []
            for question in report_input["question_catalog"]:
                question_id = question["question_id"]
                answer = {
                    "question_id": question_id,
                    "status": (
                        "ANSWERED"
                        if question["answer_scope"] == "COMPLETE"
                        else "PARTIAL"
                    ),
                    "conclusion": f"样本已按当前范围回答北极星问题 {question_id}。",
                    "metrics": [{
                        "label": "当前可核查发现",
                        "value": "1",
                        "unit": "项",
                        "method": "按当前输入中带原文依据的结构项计数。",
                        "evidence_ids": [evidence_id],
                    }],
                    "evidence_ids": [evidence_id],
                    "counter_evidence_ids": [],
                    "limitations": ["当前测试小说篇幅很短，不能外推长篇规律。"],
                    "reusable_lessons": ["先确认结构机制，再考虑是否适合自己的新书。"],
                    "do_not_copy": ["不能照搬原作人物、密信设定或具体表达。"],
                    "contract_items": [],
                }
                if question_id == "1.4":
                    promise_material = next(
                        material
                        for material in report_input["materials"]
                        if material["kind"] == "opening_promise_sources"
                    )
                    promise_sources = promise_material["item"]
                    payoff_ledger = report_input["program_artifacts"][
                        "opening_payoff_candidate_ledger"
                    ]
                    payoff_candidate = payoff_ledger["selected"]
                    representative = payoff_ledger[
                        "representative_classifications"
                    ]
                    opening_evidence_id = payoff_candidate[
                        "anchor_evidence_id"
                    ]
                    promise_evidence_ids = (
                        promise_sources["description_evidence_ids"]
                        or [opening_evidence_id]
                    )
                    first_three_evidence_id = next(
                        (
                            candidate["anchor_evidence_id"]
                            for candidate in representative
                            if (
                                1 <= int(candidate["chapter_ordinal"]) <= 3
                                and candidate["anchor_evidence_id"]
                                not in promise_evidence_ids
                            )
                        ),
                        opening_evidence_id,
                    )
                    answer["evidence_ids"] = list(dict.fromkeys([
                        *promise_evidence_ids,
                        opening_evidence_id,
                    ]))
                    answer["metrics"][0]["evidence_ids"] = [
                        opening_evidence_id
                    ]
                    answer["contract_items"] = []
                    for item in question["required_contract_items"]:
                        item_id = item["item_id"]
                        status = (
                            "INSUFFICIENT_EVIDENCE"
                            if item_id == "cross_book_comparison"
                            else "SUPPORTED"
                        )
                        finding = (
                            "缺少同品类同商业模式多书对照。"
                            if item_id == "cross_book_comparison"
                            else f"{item['label']}已由开篇原料支持。"
                        )
                        metrics = []
                        item_evidence_ids = (
                            []
                            if item_id == "cross_book_comparison"
                            else [opening_evidence_id]
                        )
                        if item_id == "opening_promise_sources":
                            title = (
                                promise_sources["preferred_title"]
                                or "源文件未提供独立书名"
                            )
                            description = (
                                "简介已核对"
                                if promise_sources["description_present"]
                                else "简介缺失"
                            )
                            finding = (
                                f"书名：{title}；{description}；"
                                "故事前提是林舟因密信进入追查；"
                                "前三章承诺他将面对旧宅秘密。"
                            )
                            metrics = [
                                {
                                    "label": "书名",
                                    "value": title,
                                    "unit": "",
                                    "method": "核对源文件前置书名。",
                                    "evidence_ids": [],
                                },
                                {
                                    "label": "简介",
                                    "value": (
                                        "简介承诺林舟将进入旧宅秘密。"
                                        if promise_sources[
                                            "description_present"
                                        ]
                                        else "源文件未识别出独立简介。"
                                    ),
                                    "unit": "",
                                    "method": "核对源文件前置简介。",
                                    "evidence_ids": promise_evidence_ids,
                                },
                                {
                                    "label": "故事前提",
                                    "value": "林舟因密信进入旧宅追查。",
                                    "unit": "",
                                    "method": "核对故事总览和开篇事件。",
                                    "evidence_ids": promise_evidence_ids,
                                },
                                {
                                    "label": "前三章",
                                    "value": "前三章以现场事件建立旧宅秘密。",
                                    "unit": "",
                                    "method": "核对前三章非简介现场原文。",
                                    "evidence_ids": [
                                        first_three_evidence_id
                                    ],
                                },
                            ]
                            item_evidence_ids = list(dict.fromkeys([
                                *promise_evidence_ids,
                                first_three_evidence_id,
                            ]))
                        if item_id == "first_payoff_location":
                            finding = "专项账本已由程序确定最早完整兑现位置。"
                            metrics = []
                            item_evidence_ids = [opening_evidence_id]
                        answer["contract_items"].append(
                        {
                            "item_id": item_id,
                            "status": status,
                            "finding": finding,
                            "metrics": metrics,
                            "evidence_ids": item_evidence_ids,
                            "limitations": (
                                ["当前只能形成单书部分观察。"]
                                if item_id == "cross_book_comparison"
                                else []
                            ),
                            "classifications": [],
                        }
                        )
                elif question_id == "2.1":
                    roles = report_input["program_artifacts"][
                        "opening_character_ledger"
                    ]["roles"]
                    answer["contract_items"] = [
                        {
                            "item_id": item["item_id"],
                            "status": "SUPPORTED",
                            "finding": f"{item['label']}已由专项原料逐项覆盖。",
                            "metrics": [],
                            "evidence_ids": [],
                            "limitations": [],
                            "classifications": [
                                {
                                    "sequence_no": role["sequence_no"],
                                    "category": "主角锚点",
                                }
                                for role in roles
                            ],
                        }
                        for item in question["required_contract_items"]
                    ]
                elif question_id == "2.2":
                    design_material = next(
                        material
                        for material in report_input["materials"]
                        if material["kind"] == "character_design_evidence"
                    )
                    design = design_material["item"]
                    field_by_id = {
                        item["field"]: item for item in design["fields"]
                    }
                    conflict_evidence_ids = [
                        evidence_id
                        for item in design["desire_conflicts"]
                        for evidence_id in item["evidence_ids"]
                    ]
                    answer["evidence_ids"] = list(dict.fromkeys(
                        evidence_id
                        for item in design["fields"]
                        for evidence_id in item["evidence_ids"]
                    ))[:24]
                    answer["metrics"] = [{
                        "label": "已核验主角要素",
                        "value": str(len(design["fields"])),
                        "unit": "项",
                        "method": "按主角专项证据账本统计。",
                        "evidence_ids": answer["evidence_ids"][:1],
                    }]
                    answer["contract_items"] = []
                    for item in question["required_contract_items"]:
                        item_id = item["item_id"]
                        if item_id in field_by_id:
                            field = field_by_id[item_id]
                            metric_value = str(
                                field["first_display_chapter_ordinal"]
                            )
                            item_evidence_ids = field["evidence_ids"]
                        else:
                            metric_value = str(
                                len(design["desire_conflicts"])
                            )
                            item_evidence_ids = conflict_evidence_ids
                        finding = f"{item['label']}已由主角行动原文支持。"
                        if item_id == "arc_timeline":
                            finding = (
                                "；".join(
                                    f"第 {conflict['chapter_ordinal']} 章形成弧光转折"
                                    for conflict in design[
                                        "desire_conflicts"
                                    ]
                                )
                                or "当前专项账本未发现可核验的弧光转折。"
                            )
                        answer["contract_items"].append({
                            "item_id": item_id,
                            "status": "SUPPORTED",
                            "finding": finding,
                            "metrics": [{
                                "label": (
                                    "首次展示章节"
                                    if item_id in field_by_id
                                    else "已核验转折节点"
                                ),
                                "value": metric_value,
                                "unit": "章" if item_id in field_by_id else "个",
                                "method": "按专项证据账本确定性统计。",
                                "evidence_ids": item_evidence_ids,
                            }],
                            "evidence_ids": item_evidence_ids,
                            "limitations": [],
                            "classifications": [],
                        })
                elif question_id == "4.9":
                    hook_summary_material = next(
                        material
                        for material in report_input["materials"]
                        if material["kind"] == "chapter_end_hooks_summary"
                    )
                    hook_summary = hook_summary_material["item"]["summary"]
                    hook_coverage = hook_summary_material["item"]["coverage"]
                    example_evidence_ids = [
                        examples[0]["ending_evidence_ids"][0]
                        for examples in hook_summary[
                            "examples_by_type"
                        ].values()
                        if examples
                    ]
                    answer["evidence_ids"] = example_evidence_ids
                    answer["metrics"] = [{
                        "label": "全书章末分类覆盖",
                        "value": str(
                            hook_coverage["source_chapter_count"]
                        ),
                        "unit": "章",
                        "method": "按连续窗口逐章分类。",
                        "evidence_ids": example_evidence_ids[:1],
                    }]
                    answer["contract_items"] = []
                    for item in question["required_contract_items"]:
                        item_id = item["item_id"]
                        metrics = []
                        item_evidence_ids = example_evidence_ids
                        finding = f"{item['label']}已由逐章章末账本支持。"
                        if item_id == "chapter_coverage":
                            metrics = [
                                {
                                    "label": "全书章节",
                                    "value": str(
                                        hook_coverage[
                                            "source_chapter_count"
                                        ]
                                    ),
                                    "unit": "章",
                                    "method": "按正式来源章节统计。",
                                    "evidence_ids": [],
                                },
                                {
                                    "label": "连续窗口",
                                    "value": str(
                                        hook_coverage["window_count"]
                                    ),
                                    "unit": "个",
                                    "method": "按正式窗口账本统计。",
                                    "evidence_ids": [],
                                },
                            ]
                        elif item_id == "type_distribution":
                            metrics = [
                                {
                                    "label": row["hook_type"],
                                    "value": str(row["count"]),
                                    "unit": f"章，占比 {row['ratio']}",
                                    "method": "按逐章类型分类统计。",
                                    "evidence_ids": [],
                                }
                                for row in hook_summary["type_distribution"]
                            ]
                        elif item_id == "strength_rhythm":
                            metrics = [
                                {
                                    "label": row["strength"],
                                    "value": str(row["count"]),
                                    "unit": "章",
                                    "method": "按逐章强度分类统计。",
                                    "evidence_ids": [],
                                }
                                for row in hook_summary[
                                    "strength_distribution"
                                ]
                            ] + [{
                                "label": "最长连续强钩",
                                "value": str(
                                    hook_summary[
                                        "max_consecutive_strong"
                                    ]
                                ),
                                "unit": "章",
                                "method": "按连续章节精确合并。",
                                "evidence_ids": [],
                            }]
                        elif item_id == "type_rotation":
                            metrics = [
                                {
                                    "label": "类型切换",
                                    "value": str(
                                        hook_summary[
                                            "type_transition_count"
                                        ]
                                    ),
                                    "unit": "次",
                                    "method": "比较相邻章节类型。",
                                    "evidence_ids": [],
                                },
                                {
                                    "label": "同类连续上限",
                                    "value": str(
                                        hook_summary[
                                            "max_consecutive_same_type"
                                        ]
                                    ),
                                    "unit": "章",
                                    "method": "按连续章节精确合并。",
                                    "evidence_ids": [],
                                },
                            ]
                        elif item_id == "no_hook_analysis":
                            metrics = [{
                                "label": "无钩章",
                                "value": str(hook_summary["no_hook_count"]),
                                "unit": (
                                    f"章，占比 {hook_summary['no_hook_ratio']}"
                                ),
                                "method": "按逐章 NONE 分类统计。",
                                "evidence_ids": [],
                            }]
                        elif item_id == "representative_examples":
                            metrics = [{
                                "label": "已覆盖类型",
                                "value": str(
                                    len(hook_summary["examples_by_type"])
                                ),
                                "unit": "种",
                                "method": "每种类型取一条真实章末原文。",
                                "evidence_ids": example_evidence_ids,
                            }]
                        elif item_id == "scope_boundary":
                            metrics = []
                            item_evidence_ids = []
                            finding = (
                                "4.9 不追踪后续回应；前三章首次回应属于 "
                                "3.4，重要悬念生命周期属于 4.10。"
                            )
                        answer["contract_items"].append({
                            "item_id": item_id,
                            "status": "SUPPORTED",
                            "finding": finding,
                            "metrics": metrics,
                            "evidence_ids": item_evidence_ids,
                            "limitations": [],
                            "classifications": [],
                        })
                answers.append(answer)
            output = {
                "answers": answers,
                "author_decisions": [],
                "method_candidates": [],
            }
        return ProviderResponse(
            raw_text=json.dumps(output, ensure_ascii=False),
            parsed=output,
            prompt_tokens=120,
            completion_tokens=80,
            parameters={},
        )


class InternalIdLeakProvider(StaticAnalysisProvider):
    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        response = await super().complete(task_kind=task_kind, payload=payload)
        if task_kind != "analysis.narrative_synthesis":
            return response
        parsed = json.loads(json.dumps(response.parsed, ensure_ascii=False))
        if "story_overview" in parsed:
            foundation = json.loads(payload["input"])
            evidence_id = foundation["events"][0]["evidence_ids"][0]
            parsed["story_overview"]["synopsis"] += f"[{evidence_id}]"
        return ProviderResponse(
            raw_text=json.dumps(parsed, ensure_ascii=False),
            parsed=parsed,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            provider_id=response.provider_id,
            model=response.model,
            parameters=response.parameters,
        )


class AuthenticationFailureProvider:
    name = "openai"

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        raise ProviderError(
            code="PROVIDER_AUTH_FAILED",
            message="API Key 无效或没有使用该模型的权限。",
            retryable=False,
        )


class FailoverExerciseProvider:
    name = "openai"

    def __init__(self, primary_service_id: str, backup_service_id: str) -> None:
        self.primary_service_id = primary_service_id
        self.backup_service_id = backup_service_id
        self.calls: list[str] = []
        self.success_provider = StaticAnalysisProvider()

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        service_id = str(payload.get("provider_service_id") or "")
        self.calls.append(service_id)
        if service_id == self.primary_service_id:
            raise ProviderError(
                code="PROVIDER_TIMEOUT",
                message="主服务模拟超时。",
                retryable=True,
                provider_name=service_id,
            )
        assert service_id == self.backup_service_id
        return await self.success_provider.complete(task_kind=task_kind, payload=payload)


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


class PartialAcceptanceFailureProvider:
    name = "fake"

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        raise ProviderError(
            code="PROVIDER_INVALID_OUTPUT",
            message="只重试未通过部分。",
            retryable=True,
            diagnostics={
                "phase": "partial_reference_validation",
                "partial_acceptance": {
                    "accepted_chapter_count": 1,
                    "repair_chapter_count": 1,
                },
                "_task_payload_patch": {
                    "accepted_chapter_proposals": [{
                        "chapter_ordinal": 1,
                    }],
                    "repair_chapter_ordinals": [2],
                },
            },
            prompt_tokens=10,
            completion_tokens=5,
            provider_name="fake",
        )


def _import_confirmed_novel(client) -> dict:
    project = client.post("/api/projects", json={"name": "雨夜旧宅"}).json()
    source = (
        "第一章 归来\n"
        "雨下得很大，林舟推开旧宅的木门。\n"
        "桌上放着一封写着他名字的密信。林舟想尽快找到寄信人，"
        "弄清来意并掌握自己的处境；虽然警惕未知风险，确认线索后仍决定"
        "主动追查，没有证据时不把猜测当成事实，并从密信线索中确定"
        "下一步行动。\n"
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


def test_partial_acceptance_payload_survives_retry_scheduling(client) -> None:
    project = client.post(
        "/api/projects",
        json={"name": "局部恢复任务测试"},
    ).json()
    with client.app.state.session_factory() as session:
        task = Task(
            project_id=project["id"],
            kind="fake.echo",
            payload_json=json.dumps(
                {"original": "kept"},
                ensure_ascii=False,
            ),
            max_attempts=2,
        )
        session.add(task)
        session.commit()
        task_id = task.id
    with client.app.state.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="partial-acceptance-worker",
            lease_seconds=60,
        )
    assert claim is not None
    assert claim.id == task_id

    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        claim,
        ProviderRegistry([PartialAcceptanceFailureProvider()]),
    )

    with client.app.state.session_factory() as session:
        persisted = session.get(Task, task_id)
        attempt = session.get(TaskAttempt, claim.current_attempt_id)
        assert persisted is not None
        assert attempt is not None
        status = persisted.status
        payload = json.loads(persisted.payload_json)
        diagnostics = json.loads(attempt.diagnostics_json)
    assert status == TaskStatus.RETRY_WAIT.value
    assert payload["original"] == "kept"
    assert payload["repair_chapter_ordinals"] == [2]
    assert payload["accepted_chapter_proposals"] == [{
        "chapter_ordinal": 1,
    }]
    assert diagnostics["partial_acceptance"] == {
        "accepted_chapter_count": 1,
        "repair_chapter_count": 1,
    }
    assert "_task_payload_patch" not in diagnostics


def test_analysis_requires_local_provider_configuration(client) -> None:
    imported = _import_confirmed_novel(client)

    response = client.post(
        f"/api/source-versions/{imported['version']['id']}/analysis/entities-events/start"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"


def test_provider_failover_waits_for_confirmation_and_resumes_same_task(client) -> None:
    imported = _import_confirmed_novel(client)
    settings = client.app.state.settings
    primary = save_model_service(
        settings,
        service_id="openai-default",
        name="主分析服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://primary.example/v1",
        api_key="sk-primary",
    )
    backup = save_model_service(
        settings,
        service_id=None,
        name="备用分析服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://backup.example/v1",
        api_key="sk-backup",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="主备分析方案",
        service_id=primary.id,
        model="primary-model",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=30,
        max_retries=2,
        failover_targets=[
            {"service_id": backup.id, "model": "backup-model"},
        ],
    )
    version_id = imported["version"]["id"]
    estimate = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events/estimate"
    ).json()
    assert estimate["retry_ceiling_call_count"] == estimate["planned_call_count"] * 6
    started = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    )
    assert started.status_code == 201
    run_id = started.json()["id"]
    provider = FailoverExerciseProvider(primary.id, backup.id)
    registry = ProviderRegistry([provider])

    for attempt_no in range(1, 4):
        with client.app.state.session_factory() as session:
            claim = claim_next_task(
                session,
                worker_id=f"primary-failure-{attempt_no}",
                lease_seconds=60,
            )
        assert claim is not None
        assert execute_task_sync(
            client.app.state.session_factory,
            settings,
            claim,
            registry,
        )

    waiting = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    ).json()
    assert waiting["status"] == "WAITING_CONFIRMATION"
    assert waiting["provider_confirmation"] == {
        "current_service_name": "主分析服务",
        "current_model": "primary-model",
        "next_service_name": "备用分析服务",
        "next_model": "backup-model",
        "failure_count": 3,
        "threshold": 3,
        "error_code": "PROVIDER_TIMEOUT",
        "message": "主服务模拟超时。",
    }

    retry_current = client.post(
        f"/api/analysis-runs/{run_id}/provider-switch",
        json={"decision": "RETRY_CURRENT"},
    )
    assert retry_current.status_code == 200
    assert retry_current.json()["status"] == "PENDING"
    assert retry_current.json()["provider_confirmation"] is None

    for attempt_no in range(4, 7):
        with client.app.state.session_factory() as session:
            claim = claim_next_task(
                session,
                worker_id=f"primary-retry-{attempt_no}",
                lease_seconds=60,
            )
        assert claim is not None
        assert execute_task_sync(
            client.app.state.session_factory,
            settings,
            claim,
            registry,
        )

    waiting_again = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    ).json()
    assert waiting_again["status"] == "WAITING_CONFIRMATION"
    assert waiting_again["provider_confirmation"]["failure_count"] == 3

    switched = client.post(
        f"/api/analysis-runs/{run_id}/provider-switch",
        json={"decision": "SWITCH"},
    )
    assert switched.status_code == 200
    assert switched.json()["status"] == "PENDING"
    assert switched.json()["provider_confirmation"] is None

    with client.app.state.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="backup-success",
            lease_seconds=60,
        )
    assert claim is not None
    original_task_id = claim.id
    assert execute_task_sync(
        client.app.state.session_factory,
        settings,
        claim,
        registry,
    )
    assert provider.calls == [primary.id] * 6 + [backup.id]
    with client.app.state.session_factory() as session:
        task = session.get(Task, original_task_id)
        assert task is not None
        assert task.status == TaskStatus.SUCCEEDED.value
        assert task.attempts == 7
        payload = json.loads(task.payload_json)
        assert payload["provider_route_index"] == 1
        assert [item["decision"] for item in payload["provider_switch_history"]] == [
            "RETRY_CURRENT",
            "SWITCH",
        ]


def test_permanent_provider_failure_can_be_stopped_without_switching(client) -> None:
    imported = _import_confirmed_novel(client)
    settings = client.app.state.settings
    primary = save_model_service(
        settings,
        service_id="openai-default",
        name="权限失效服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://primary.example/v1",
        api_key="sk-primary",
    )
    backup = save_model_service(
        settings,
        service_id=None,
        name="未启用备用服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://backup.example/v1",
        api_key="sk-backup",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="权限失败停止方案",
        service_id=primary.id,
        model="primary-model",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=30,
        max_retries=2,
        failover_targets=[{"service_id": backup.id, "model": "backup-model"}],
    )
    version_id = imported["version"]["id"]
    run = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    ).json()
    with client.app.state.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="auth-failure",
            lease_seconds=60,
        )
    assert claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        settings,
        claim,
        ProviderRegistry([AuthenticationFailureProvider()]),
    )

    waiting = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    ).json()
    assert waiting["status"] == "WAITING_CONFIRMATION"
    assert waiting["provider_confirmation"]["failure_count"] == 1
    assert waiting["provider_confirmation"]["error_code"] == "PROVIDER_AUTH_FAILED"

    stopped = client.post(
        f"/api/analysis-runs/{run['id']}/provider-switch",
        json={"decision": "STOP"},
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "FAILED"
    assert stopped.json()["provider_confirmation"] is None
    with client.app.state.session_factory() as session:
        task = session.get(Task, claim.id)
        assert task is not None
        assert task.status == TaskStatus.FAILED.value
        assert task.error_code == "PROVIDER_SWITCH_DECLINED"


def test_analysis_estimate_reports_local_batches_and_token_ceiling(client) -> None:
    imported = _import_confirmed_novel(client)
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="用量估算服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://provider.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="人物与事件精确提取",
        service_id=service.id,
        model="usage-model",
        temperature=None,
        max_output_tokens=4_000,
        reasoning_effort="auto",
        timeout_seconds=60,
        max_retries=2,
        context_window_tokens=32_000,
    )

    response = client.get(
        f"/api/source-versions/{imported['version']['id']}/analysis/entities-events/estimate"
    )

    assert response.status_code == 200
    estimate = response.json()
    assert estimate["batch_count"] == 1
    assert estimate["planned_call_count"] == 6
    assert estimate["retry_ceiling_call_count"] == 18
    assert estimate["estimated_input_tokens"] > 0
    assert estimate["maximum_output_tokens"] == 24_000


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


def test_entities_events_flow_keeps_exact_source_evidence_and_is_idempotent(
    client,
    monkeypatch,
) -> None:
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
    assert progress["total_batches"] == 5

    narrative_components = set()
    for index in range(4):
        with client.app.state.session_factory() as session:
            narrative_claim = claim_next_task(
                session,
                worker_id=f"narrative-test-worker-{index}",
                lease_seconds=60,
            )
        assert narrative_claim is not None
        assert narrative_claim.kind == "analysis.narrative_synthesis"
        narrative_components.add(json.loads(narrative_claim.payload_json)["narrative_component"])
        assert execute_task_sync(
            client.app.state.session_factory,
            client.app.state.settings,
            narrative_claim,
            registry,
        )
    assert narrative_components == {"overview", "characters", "plot", "relations"}

    progress = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    ).json()
    assert progress["status"] == "REVIEW"
    assert progress["completed_batches"] == 5
    assert progress["failed_batches"] == 0
    assert progress["total_batches"] == 5

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
    assert continue_analysis.json()["total_batches"] == 6

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
    assert progress["completed_batches"] == 6
    assert progress["failed_batches"] == 0

    diagnostics = client.get(
        f"/api/analysis-runs/{run['id']}/diagnostics"
    ).json()
    assert diagnostics["attempt_count"] == 6
    assert diagnostics["retry_count"] == 0
    assert diagnostics["duration_seconds"] >= 0
    assert diagnostics["prompt_tokens"] == 720
    assert diagnostics["completion_tokens"] == 480
    assert [item["prompt_tokens"] for item in diagnostics["stages"]] == [120, 480, 120]
    assert [item["completion_tokens"] for item in diagnostics["stages"]] == [80, 320, 80]
    assert [item["status"] for item in diagnostics["stages"]] == [
        "SUCCEEDED",
        "SUCCEEDED",
        "SUCCEEDED",
    ]
    narrative_stage = next(
        item for item in diagnostics["stages"]
        if item["key"] == "analysis.narrative_synthesis"
    )
    assert len(narrative_stage["calls"]) == 4
    assert narrative_stage["duration_seconds"] >= 0
    overview_call = next(
        item for item in narrative_stage["calls"]
        if item["component"] == "overview"
    )
    assert overview_call["component_label"] == "故事总览"
    assert overview_call["duration_seconds"] >= 0
    call_content = client.get(
        f"/api/analysis-runs/{run['id']}/attempts/{overview_call['attempt_id']}/content"
    )
    assert call_content.status_code == 200
    assert "requested_component" in call_content.json()["input_text"]
    assert "story_overview" in call_content.json()["output_text"]

    state_at_chapter = client.get(
        f"/api/analysis-runs/{run['id']}/state-at-chapter?chapter_ordinal=2"
    )
    assert state_at_chapter.status_code == 200
    state_payload = state_at_chapter.json()
    assert state_payload["chapter_title"].startswith("第二章")
    assert state_payload["states"][0]["chapter_ordinal"] == 2
    assert state_payload["knowledge"][0]["actor"] == "林舟"
    assert state_payload["knowledge_transfers"][0]["source_actor"] == "直接观察"
    assert state_payload["knowledge_transfers"][0]["target_actor"] == "林舟"

    completed_task = client.get(f"/api/tasks/{claim.id}").json()
    artifact = client.get(
        f"/api/artifacts/{completed_task['result_artifact_id']}/content"
    ).json()
    assert artifact["request"]["prompt_id"] == "entities_events"
    assert artifact["request"]["prompt_version"] == "1.3.0"
    assert artifact["request"]["source_version_id"] == version_id
    assert len(artifact["request"]["input_sha256"]) == 64
    assert "参与事件的人物" in artifact["request"]["instructions"]
    assert "林舟推开旧宅的木门" in artifact["request"]["input"]

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
    assert projection["events"][0]["discovery_routes"] == ["INFORMATION_CHANGE"]
    assert len(projection["phases"]) == 1
    assert projection["phases"][0]["event_ids"] == [projection["events"][0]["id"]]
    assert projection["phases"][0]["title"] == "雨夜归来与密信出现"
    assert projection["deep_status"] == "READY"
    assert projection["deep_analysis"]["fact_versions"][0]["status"] == "CONFIRMED"
    assert projection["deep_analysis"]["knowledge_transfers"][0]["transfer_type"] == "WITNESSED"
    unaffected_fact = next(
        item
        for item in projection["deep_analysis"]["fact_versions"]
        if item["subject"] == "旧宅"
    )
    assert projection["deep_analysis"]["claims"][0]["verification_status"] == "SUPPORTED"
    assert projection["deep_analysis"]["world_rules"][0]["discovered_chapter"] == 1
    assert projection["character_design_status"] == "NOT_GENERATED"
    assert projection["learning_report"]["readiness"]["ready_question_count"] == 1

    character_start = client.post(
        f"/api/analysis-runs/{run['id']}/character-design/start"
    )
    assert character_start.status_code == 202
    with client.app.state.session_factory() as session:
        character_fields_claim = claim_next_task(
            session,
            worker_id="character-design-test-worker",
            lease_seconds=60,
        )
    assert character_fields_claim is not None
    assert character_fields_claim.kind == "analysis.character_design_evidence"
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        character_fields_claim,
        registry,
    )
    character_fields_projection = client.get(
        f"/api/analysis-runs/{run['id']}/workbench"
    ).json()
    assert character_fields_projection["character_design_status"] == "GENERATING"
    with client.app.state.session_factory() as session:
        character_claim = claim_next_task(
            session,
            worker_id="character-conflicts-test-worker",
            lease_seconds=60,
        )
    assert character_claim is not None
    assert character_claim.kind == "analysis.character_design_evidence"
    assert json.loads(character_claim.payload_json)["window_phase"] == "CONFLICTS"
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        character_claim,
        registry,
    )
    character_projection = client.get(
        f"/api/analysis-runs/{run['id']}/workbench"
    ).json()
    character_task_state = client.get(
        f"/api/tasks/{character_claim.id}"
    ).json()
    assert character_projection["character_design_status"] == "READY", (
        character_task_state["last_error_code"],
        character_task_state["last_error_message"],
    )
    assert character_projection["character_design_evidence"]["revision"] == 1
    assert character_projection["character_design_evidence"]["is_current"] is True
    assert len(character_projection["character_design_evidence"]["fields"]) == 6
    assert character_projection["character_design_evidence"]["coverage"]["event_coverage_complete"] is True
    assert character_projection["learning_report"]["readiness"]["ready_question_count"] == 2
    with client.app.state.session_factory() as session:
        cancelled_character_task = Task(
            project_id=character_claim.project_id,
            kind="analysis.character_design_evidence",
            status=TaskStatus.CANCELLED.value,
            payload_json="{}",
            max_attempts=1,
        )
        session.add(cancelled_character_task)
        session.flush()
        session.add(AnalysisRunTask(
            run_id=run["id"],
            task_id=cancelled_character_task.id,
            batch_index=999,
        ))
        session.commit()
    character_diagnostics = client.get(
        f"/api/analysis-runs/{run['id']}/diagnostics"
    ).json()
    character_stage = next(
        item for item in character_diagnostics["stages"]
        if item["key"] == "analysis.character_design_evidence"
    )
    assert character_stage["status"] == "SUCCEEDED"
    already_current = client.post(
        f"/api/analysis-runs/{run['id']}/character-design/start"
    )
    assert already_current.status_code == 409
    assert already_current.json()["detail"]["code"] == "CHARACTER_DESIGN_ALREADY_CURRENT"

    monkeypatch.setattr(
        chapter_end_hooks_service,
        "CHAPTER_END_HOOK_MAX_WINDOW_CHAPTERS",
        1,
    )
    hooks_start = client.post(
        f"/api/analysis-runs/{run['id']}/chapter-end-hooks/start"
    )
    assert hooks_start.status_code == 202
    hooks_claim = None
    for index in range(2):
        with client.app.state.session_factory() as session:
            hooks_claim = claim_next_task(
                session,
                worker_id=f"chapter-end-hooks-test-worker-{index}",
                lease_seconds=60,
            )
        assert hooks_claim is not None
        assert hooks_claim.kind == "analysis.chapter_end_hooks"
        assert execute_task_sync(
            client.app.state.session_factory,
            client.app.state.settings,
            hooks_claim,
            registry,
        )
        if index == 0:
            partial_hooks_projection = client.get(
                f"/api/analysis-runs/{run['id']}/workbench"
            ).json()
            assert partial_hooks_projection["chapter_end_hooks_status"] == "GENERATING"
            assert partial_hooks_projection["chapter_end_hooks_evidence"] is None
    assert hooks_claim is not None
    hooks_projection = client.get(
        f"/api/analysis-runs/{run['id']}/workbench"
    ).json()
    assert hooks_projection["chapter_end_hooks_status"] == "READY"
    assert hooks_projection["chapter_end_hooks_evidence"]["revision"] == 1
    assert hooks_projection["chapter_end_hooks_evidence"]["is_current"] is True
    assert hooks_projection["chapter_end_hooks_evidence"]["coverage"]["sampled_chapter_count"] == 2
    assert len(hooks_projection["chapter_end_hooks"]) == 2
    assert hooks_projection["learning_report"]["readiness"]["ready_question_count"] == 3
    hooks_diagnostics = client.get(
        f"/api/analysis-runs/{run['id']}/diagnostics"
    ).json()
    hooks_stage = next(
        item for item in hooks_diagnostics["stages"]
        if item["key"] == "analysis.chapter_end_hooks"
    )
    assert hooks_stage["status"] == "SUCCEEDED"
    hooks_already_current = client.post(
        f"/api/analysis-runs/{run['id']}/chapter-end-hooks/start"
    )
    assert hooks_already_current.status_code == 409
    assert hooks_already_current.json()["detail"]["code"] == "CHAPTER_END_HOOKS_ALREADY_CURRENT"

    with client.app.state.session_factory() as session:
        learning_task_count_before_character_revision = session.scalar(
            select(func.count(Task.id)).where(Task.kind == "analysis.learning_report")
        )
        payoff_task_count_before = session.scalar(
            select(func.count(Task.id)).where(
                Task.kind == "analysis.opening_payoff_candidates"
            )
        )
    first_learning_start = client.post(
        f"/api/analysis-runs/{run['id']}/learning-report/start"
    )
    assert first_learning_start.status_code == 202
    with client.app.state.session_factory() as session:
        assert session.scalar(
            select(func.count(Task.id)).where(Task.kind == "analysis.learning_report")
        ) == learning_task_count_before_character_revision + 3
        assert session.scalar(
            select(func.count(Task.id)).where(
                Task.kind == "analysis.opening_payoff_candidates"
            )
        ) == payoff_task_count_before + 1
        opening_payoff_claim = claim_next_task(
            session,
            worker_id="opening-payoff-test-worker",
            lease_seconds=60,
        )
    assert opening_payoff_claim is not None
    assert opening_payoff_claim.kind == "analysis.opening_payoff_candidates"
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        opening_payoff_claim,
        registry,
    )
    with client.app.state.session_factory() as session:
        assert session.scalar(
            select(func.count(Task.id)).where(Task.kind == "analysis.learning_report")
        ) == learning_task_count_before_character_revision + 4
    first_learning_claim = None
    first_question_ids = []
    for index in range(4):
        with client.app.state.session_factory() as session:
            current_claim = claim_next_task(
                session,
                worker_id=f"first-incremental-learning-worker-{index}",
                lease_seconds=60,
            )
        assert current_claim is not None
        assert current_claim.kind == "analysis.learning_report"
        question_ids = json.loads(current_claim.payload_json)["question_ids"]
        assert len(question_ids) == 1
        first_question_ids.extend(question_ids)
        first_learning_claim = current_claim
        assert execute_task_sync(
            client.app.state.session_factory,
            client.app.state.settings,
            current_claim,
            registry,
        )
    assert first_question_ids == ["2.1", "2.2", "4.9", "1.4"]
    assert first_learning_claim is not None
    first_learning_projection = client.get(
        f"/api/analysis-runs/{run['id']}/workbench"
    ).json()
    first_learning_task_state = client.get(
        f"/api/tasks/{first_learning_claim.id}"
    ).json()
    with client.app.state.session_factory() as session:
        first_learning_attempt = session.get(
            TaskAttempt,
            first_learning_claim.current_attempt_id,
        )
        first_learning_diagnostics = (
            json.loads(first_learning_attempt.diagnostics_json)
            if first_learning_attempt is not None
            else {}
        )
        first_learning_task_states = [
            (
                item.id,
                item.status,
                item.last_error_code,
                item.last_error_message,
            )
            for item in session.scalars(
                select(Task)
                .where(Task.kind == "analysis.learning_report")
                .order_by(Task.created_at)
            )
        ]
    assert first_learning_projection["learning_report_status"] == "READY", (
        first_learning_task_state["last_error_code"],
        first_learning_task_state["last_error_message"],
        first_learning_diagnostics.get("reason_code"),
        first_learning_task_states,
    )
    first_pipeline_diagnostics = client.get(
        f"/api/analysis-runs/{run['id']}/diagnostics"
    ).json()
    opening_payoff_stage = next(
        item for item in first_pipeline_diagnostics["stages"]
        if item["key"] == "analysis.opening_payoff_candidates"
    )
    assert opening_payoff_stage["status"] == "SUCCEEDED"
    assert len(opening_payoff_stage["calls"]) == 1
    assert sum(
        item["generated_count"]
        for item in first_learning_projection["learning_report"]["stages"]
    ) == 4
    first_2_1 = next(
        item
        for item in first_learning_projection["learning_report"]["questions"]
        if item["question_id"] == "2.1"
    )
    assert first_2_1["conclusion"].startswith(
        "前 3、10、30 章分别有 1、1、1 名具名人物参与有效行动"
    )
    assert "指数" not in first_2_1["conclusion"]
    assert "当前测试小说篇幅很短" not in first_2_1["limitations"]
    assert first_2_1["reusable_lessons"]
    assert first_2_1["do_not_copy"]
    first_2_2 = next(
        item
        for item in first_learning_projection["learning_report"]["questions"]
        if item["question_id"] == "2.2"
    )
    assert first_2_2["conclusion"].startswith(
        "主角双层欲望与最小完整集均已按首次行动证据定位"
    )
    assert "当前可核查发现" not in {
        metric["label"] for metric in first_2_2["metrics"]
    }
    assert {
        "表层欲望首次展示章节",
        "深层欲望首次展示章节",
        "核心能力首次展示章节",
        "双层欲望冲突节点",
    }.issubset({
        metric["label"] for metric in first_2_2["metrics"]
    })
    first_4_9 = next(
        item
        for item in first_learning_projection["learning_report"]["questions"]
        if item["question_id"] == "4.9"
    )
    expected_hook_chapters = str(
        hooks_projection["chapter_end_hooks_evidence"]["coverage"][
            "source_chapter_count"
        ]
    )
    chapter_metric = next(
        metric
        for metric in first_4_9["metrics"]
        if metric["label"] == "全书章节"
    )
    assert chapter_metric["value"] == expected_hook_chapters
    assert first_4_9["conclusion"].startswith(
        f"全书 {expected_hook_chapters} 章"
    )
    assert "当前可核查发现" not in {
        metric["label"] for metric in first_4_9["metrics"]
    }

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
    impact = client.get(f"/api/analysis-runs/{run['id']}/deep/recompute-impact")
    assert impact.status_code == 200
    assert impact.json()["mode"] == "TARGETED"
    assert impact.json()["issue_count"] == 1
    fact_impact = next(
        item for item in impact.json()["sections"] if item["key"] == "fact_versions"
    )
    assert fact_impact["item_labels"] == ["林舟：当前目标"]
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
        "knowledge_transfers",
        "claims",
    ]
    assert revision_payload["revision_impact"]["mode"] == "TARGETED"
    assert revision_payload["revision_impact"]["sections"][0]["key"] == "fact_versions"
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
    assert diff["changed"]["fact_versions"] == ["林舟：当前目标"]
    first_revision = client.get(
        f"/api/analysis-runs/{run['id']}/workbench?deep_revision=1"
    )
    assert first_revision.status_code == 200
    assert first_revision.json()["deep_revision"] == 1
    latest_revision = client.get(f"/api/analysis-runs/{run['id']}/workbench")
    assert latest_revision.json()["deep_revision"] == 2
    assert latest_revision.json()["character_design_status"] == "OUTDATED"
    assert latest_revision.json()["character_design_evidence"]["is_current"] is False
    assert latest_revision.json()["chapter_end_hooks_status"] == "OUTDATED"
    assert latest_revision.json()["chapter_end_hooks_evidence"]["is_current"] is False
    assert latest_revision.json()["learning_report_status"] == "OUTDATED"
    assert latest_revision.json()["learning_report"]["readiness"]["ready_question_count"] == 1
    assert latest_revision.json()["deep_analysis"]["conflicts"][0]["resolution"] == "尚未解决。"
    latest_unaffected_fact = next(
        item
        for item in latest_revision.json()["deep_analysis"]["fact_versions"]
        if item["subject"] == "旧宅"
    )
    assert latest_unaffected_fact["value"] == "木门老旧"
    assert latest_unaffected_fact["id"] == unaffected_fact["id"]
    missing_revision = client.get(
        f"/api/analysis-runs/{run['id']}/workbench?deep_revision=999"
    )
    assert missing_revision.status_code == 404
    assert missing_revision.json()["detail"]["code"] == "DEEP_ANALYSIS_REVISION_NOT_FOUND"

    refreshed_character_start = client.post(
        f"/api/analysis-runs/{run['id']}/character-design/start"
    )
    assert refreshed_character_start.status_code == 202
    with client.app.state.session_factory() as session:
        refreshed_character_fields_claim = claim_next_task(
            session,
            worker_id="character-design-refresh-worker",
            lease_seconds=60,
        )
    assert refreshed_character_fields_claim is not None
    assert refreshed_character_fields_claim.kind == "analysis.character_design_evidence"
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        refreshed_character_fields_claim,
        registry,
    )
    with client.app.state.session_factory() as session:
        refreshed_character_claim = claim_next_task(
            session,
            worker_id="character-conflicts-refresh-worker",
            lease_seconds=60,
        )
    assert refreshed_character_claim is not None
    assert refreshed_character_claim.kind == "analysis.character_design_evidence"
    assert json.loads(refreshed_character_claim.payload_json)["window_phase"] == "CONFLICTS"
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        refreshed_character_claim,
        registry,
    )
    current_revision = client.get(f"/api/analysis-runs/{run['id']}/workbench")
    assert current_revision.status_code == 200
    assert current_revision.json()["character_design_status"] == "READY"
    assert current_revision.json()["character_design_evidence"]["revision"] == 2

    initial_learning = current_revision.json()["learning_report"]
    assert current_revision.json()["learning_report_status"] == "OUTDATED"
    assert len(initial_learning["questions"]) == 42
    assert sum(item["status"] == "OUTDATED" for item in initial_learning["questions"]) == 4
    assert initial_learning["readiness"]["ready"] is True
    assert initial_learning["readiness"]["ready_question_count"] == 2
    with client.app.state.session_factory() as session:
        learning_task_count_before = session.scalar(
            select(func.count(Task.id)).where(Task.kind == "analysis.learning_report")
        )
    learning_start = client.post(
        f"/api/analysis-runs/{run['id']}/learning-report/start"
    )
    assert learning_start.status_code == 202
    with client.app.state.session_factory() as session:
        learning_task_count_after = session.scalar(
            select(func.count(Task.id)).where(Task.kind == "analysis.learning_report")
        )
        refreshed_payoff_claim = claim_next_task(
            session,
            worker_id="opening-payoff-refresh-worker",
            lease_seconds=60,
        )
    assert learning_task_count_after == learning_task_count_before + 2
    assert refreshed_payoff_claim is not None
    assert refreshed_payoff_claim.kind == "analysis.opening_payoff_candidates"
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        refreshed_payoff_claim,
        registry,
    )
    with client.app.state.session_factory() as session:
        assert session.scalar(
            select(func.count(Task.id)).where(Task.kind == "analysis.learning_report")
        ) == learning_task_count_before + 3
    refreshed_question_ids = []
    for index in range(3):
        with client.app.state.session_factory() as session:
            refreshed_learning_claim = claim_next_task(
                session,
                worker_id=f"refreshed-incremental-learning-worker-{index}",
                lease_seconds=60,
            )
        assert refreshed_learning_claim is not None
        question_ids = json.loads(refreshed_learning_claim.payload_json)["question_ids"]
        assert len(question_ids) == 1
        refreshed_question_ids.extend(question_ids)
        assert execute_task_sync(
            client.app.state.session_factory,
            client.app.state.settings,
            refreshed_learning_claim,
            registry,
        )
    assert refreshed_question_ids == ["2.1", "2.2", "1.4"]
    partial_refresh = client.get(
        f"/api/analysis-runs/{run['id']}/workbench"
    ).json()
    assert partial_refresh["learning_report_status"] == "READY"
    assert sum(
        item["generated_count"]
        for item in partial_refresh["learning_report"]["stages"]
    ) == 3

    refreshed_hooks_start = client.post(
        f"/api/analysis-runs/{run['id']}/chapter-end-hooks/start"
    )
    assert refreshed_hooks_start.status_code == 202
    refreshed_hooks_claim = None
    for index in range(2):
        with client.app.state.session_factory() as session:
            refreshed_hooks_claim = claim_next_task(
                session,
                worker_id=f"chapter-end-hooks-refresh-worker-{index}",
                lease_seconds=60,
            )
        assert refreshed_hooks_claim is not None
        assert execute_task_sync(
            client.app.state.session_factory,
            client.app.state.settings,
            refreshed_hooks_claim,
            registry,
        )
    assert refreshed_hooks_claim is not None
    hooks_refresh_projection = client.get(
        f"/api/analysis-runs/{run['id']}/workbench"
    ).json()
    assert hooks_refresh_projection["learning_report_status"] == "OUTDATED"
    assert hooks_refresh_projection["learning_report"]["readiness"]["ready_question_count"] == 4

    hook_answers_start = client.post(
        f"/api/analysis-runs/{run['id']}/learning-report/start"
    )
    assert hook_answers_start.status_code == 202
    with client.app.state.session_factory() as session:
        hook_answers_claim = claim_next_task(
            session,
            worker_id="hook-answer-incremental-worker",
            lease_seconds=60,
        )
    assert hook_answers_claim is not None
    assert json.loads(hook_answers_claim.payload_json)["question_ids"] == ["4.9"]
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        hook_answers_claim,
        registry,
    )
    merged_learning = client.get(
        f"/api/analysis-runs/{run['id']}/workbench"
    ).json()
    assert merged_learning["learning_report_status"] == "READY"
    assert sum(
        item["generated_count"]
        for item in merged_learning["learning_report"]["stages"]
    ) == 4

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


def test_deep_start_creates_recovery_task_after_initial_failure(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-test"})
    version_id = imported["version"]["id"]
    run = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    ).json()
    registry = ProviderRegistry([StaticAnalysisProvider()])

    with client.app.state.session_factory() as session:
        foundation_claim = claim_next_task(
            session,
            worker_id="deep-recovery-foundation",
            lease_seconds=60,
        )
    assert foundation_claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        foundation_claim,
        registry,
    )

    for index in range(4):
        with client.app.state.session_factory() as session:
            narrative_claim = claim_next_task(
                session,
                worker_id=f"deep-recovery-narrative-{index}",
                lease_seconds=60,
            )
        assert narrative_claim is not None
        assert execute_task_sync(
            client.app.state.session_factory,
            client.app.state.settings,
            narrative_claim,
            registry,
        )

    first_start = client.post(f"/api/analysis-runs/{run['id']}/deep/start")
    assert first_start.status_code == 202
    with client.app.state.session_factory() as session:
        failed_claim = claim_next_task(
            session,
            worker_id="deep-recovery-first-attempt",
            lease_seconds=60,
        )
    assert failed_claim is not None
    assert failed_claim.kind == "analysis.deep_insights"
    with client.app.state.session_factory() as session:
        assert fail_task_attempt(
            session,
            task_id=failed_claim.id,
            attempt_id=failed_claim.current_attempt_id,
            lease_token=failed_claim.lease_token,
            lease_generation=failed_claim.lease_generation,
            error_code="PROVIDER_UNAVAILABLE",
            error_message="模拟首次深层任务耗尽。",
            retryable=False,
            retry_after_seconds=None,
        )

    restarted = client.post(f"/api/analysis-runs/{run['id']}/deep/start")
    assert restarted.status_code == 202
    assert restarted.json()["status"] == "PENDING"
    assert restarted.json()["total_batches"] == 7
    with client.app.state.session_factory() as session:
        deep_tasks = list(session.scalars(
            select(Task)
            .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
            .where(
                AnalysisRunTask.run_id == run["id"],
                Task.kind == "analysis.deep_insights",
            )
            .order_by(AnalysisRunTask.batch_index)
        ))
    assert [task.status for task in deep_tasks] == [
        TaskStatus.FAILED.value,
        TaskStatus.PENDING.value,
    ]

    with client.app.state.session_factory() as session:
        recovery_claim = claim_next_task(
            session,
            worker_id="deep-recovery-second-attempt",
            lease_seconds=60,
        )
    assert recovery_claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        recovery_claim,
        registry,
    )
    diagnostics = client.get(
        f"/api/analysis-runs/{run['id']}/diagnostics"
    ).json()
    deep_stage = next(
        item for item in diagnostics["stages"]
        if item["key"] == "analysis.deep_insights"
    )
    assert deep_stage["status"] == "SUCCEEDED"
    assert diagnostics["current_step"] == "全部分析已经完成"


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


def test_failed_update_keeps_last_usable_workbench_in_review(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-test"})
    version_id = imported["version"]["id"]
    run = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    ).json()
    registry = ProviderRegistry([StaticAnalysisProvider()])

    for index in range(5):
        with client.app.state.session_factory() as session:
            claim = claim_next_task(
                session, worker_id=f"usable-result-worker-{index}", lease_seconds=60
            )
        assert claim is not None
        assert execute_task_sync(
            client.app.state.session_factory,
            client.app.state.settings,
            claim,
            registry,
        )

    with client.app.state.session_factory() as session:
        analysis_run = session.get(AnalysisRun, run["id"])
        assert analysis_run is not None
        failed_task = Task(
            project_id=analysis_run.source_version.document.project_id,
            kind="analysis.narrative_synthesis",
            payload_json=json.dumps({
                "run_id": run["id"],
                "source_version_id": version_id,
                "narrative_component": "overview",
            }),
            status=TaskStatus.FAILED.value,
            max_attempts=1,
            attempts=1,
            last_error_code="PROVIDER_INVALID_OUTPUT",
            last_error_message="最近一次故事总览更新格式不完整。",
        )
        session.add(failed_task)
        session.flush()
        next_index = analysis_run.total_batches + 1
        session.add(AnalysisRunTask(
            run_id=run["id"], task_id=failed_task.id, batch_index=next_index
        ))
        analysis_run.total_batches = next_index
        session.commit()

    result = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    )
    assert result.status_code == 200
    payload = result.json()
    assert payload["status"] == "REVIEW"
    assert payload["has_usable_result"] is True
    assert payload["usable_result_level"] == "STORY"
    assert payload["latest_update_failed"] is True
    assert payload["failure_code"] == "PROVIDER_INVALID_OUTPUT"
    workbench = client.get(f"/api/analysis-runs/{run['id']}/workbench")
    assert workbench.status_code == 200
    assert workbench.json()["narrative_status"] == "READY"

    with client.app.state.session_factory() as session:
        shadow_run = AnalysisRun(
            source_version_id=version_id,
            stage="ENTITIES_EVENTS",
            status=AnalysisRunStatus.PENDING.value,
            total_batches=1,
        )
        session.add(shadow_run)
        session.flush()
        cancelled_task = Task(
            project_id=shadow_run.source_version.document.project_id,
            kind="analysis.entities_events",
            payload_json="{}",
            status=TaskStatus.CANCELLED.value,
            max_attempts=1,
        )
        session.add(cancelled_task)
        session.flush()
        session.add(AnalysisRunTask(
            run_id=shadow_run.id,
            task_id=cancelled_task.id,
            batch_index=1,
        ))
        session.commit()

    preferred = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    ).json()
    assert preferred["id"] == run["id"]
    assert preferred["has_usable_result"] is True


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
    for index in range(4):
        with client.app.state.session_factory() as session:
            narrative_claim = claim_next_task(
                session, worker_id=f"legacy-narrative-worker-{index}", lease_seconds=60
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
    repair_components = set()
    for index in range(4):
        with client.app.state.session_factory() as session:
            repair_claim = claim_next_task(
                session, worker_id=f"legacy-repair-worker-{index}", lease_seconds=60
            )
        assert repair_claim is not None
        assert repair_claim.kind == "analysis.narrative_synthesis"
        assert "人物角色覆盖" in repair_claim.payload_json
        repair_components.add(json.loads(repair_claim.payload_json)["narrative_component"])
        assert execute_task_sync(
            client.app.state.session_factory,
            client.app.state.settings,
            repair_claim,
            registry,
        )
    assert repair_components == {"overview", "characters", "plot", "relations"}
    with client.app.state.session_factory() as session:
        deep_tasks = list(session.scalars(
            select(Task)
            .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
            .where(
                AnalysisRunTask.run_id == run["id"],
                Task.kind == "analysis.deep_insights",
            )
        ))
    assert len(deep_tasks) == 1

    repaired = client.get(f"/api/analysis-runs/{run['id']}/workbench")
    assert repaired.status_code == 200
    repaired_payload = repaired.json()
    assert repaired_payload["narrative_status"] == "READY"
    assert all(item["role"] != "UNCLASSIFIED" for item in repaired_payload["characters"])
    assert repaired_payload["story_overview"]["development_path"]
    assert repaired_payload["story_overview"]["turning_points"]
    assert repaired_payload["story_overview"]["current_result"]


def test_narrative_component_can_be_retried_without_rebuilding_other_sections(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-test"})
    run = client.post(
        f"/api/source-versions/{imported['version']['id']}/analysis/entities-events/start"
    ).json()
    registry = ProviderRegistry([StaticAnalysisProvider()])
    for index in range(5):
        with client.app.state.session_factory() as session:
            claim = claim_next_task(
                session, worker_id=f"component-retry-setup-{index}", lease_seconds=60
            )
        assert claim is not None
        assert execute_task_sync(
            client.app.state.session_factory,
            client.app.state.settings,
            claim,
            registry,
        )

    with client.app.state.session_factory() as session:
        synthesis = session.scalar(
            select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run["id"])
        )
        assert synthesis is not None
        payload = json.loads(synthesis.payload_json)
        payload["story_overview"]["synopsis"] += "[evd_1234567890abcdef1234567890abcdef]"
        synthesis.payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        session.commit()

    cleaned = client.get(f"/api/analysis-runs/{run['id']}/workbench").json()
    assert "evd_" not in cleaned["story_overview"]["synopsis"]

    retry = client.post(
        f"/api/analysis-runs/{run['id']}/narrative/components/overview/retry"
    )
    assert retry.status_code == 202
    with client.app.state.session_factory() as session:
        pending = list(session.scalars(
            select(Task)
            .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
            .where(
                AnalysisRunTask.run_id == run["id"],
                Task.status == TaskStatus.PENDING.value,
            )
        ))
    assert len(pending) == 1
    retry_payload = json.loads(pending[0].payload_json)
    assert retry_payload["narrative_component"] == "overview"
    assert retry_payload["narrative_components"] == ["overview"]
    assert retry_payload["enqueue_deep_after_narrative"] is False

    with client.app.state.session_factory() as session:
        retry_claim = claim_next_task(
            session, worker_id="component-retry-worker", lease_seconds=60
        )
    assert retry_claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        retry_claim,
        registry,
    )
    with client.app.state.session_factory() as session:
        deep_tasks = list(session.scalars(
            select(Task)
            .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
            .where(
                AnalysisRunTask.run_id == run["id"],
                Task.kind == "analysis.deep_insights",
            )
        ))
    assert deep_tasks == []
    refreshed = client.get(f"/api/analysis-runs/{run['id']}/workbench").json()
    assert refreshed["story_overview"]["synopsis"].startswith("林舟回到旧宅")

    assert client.post(f"/api/analysis-runs/{run['id']}/deep/start").status_code == 202
    with client.app.state.session_factory() as session:
        deep_claim = claim_next_task(
            session, worker_id="component-retry-initial-deep", lease_seconds=60
        )
    assert deep_claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        deep_claim,
        registry,
    )
    assert client.get(f"/api/analysis-runs/{run['id']}/workbench").json()["deep_status"] == "READY"

    second_retry = client.post(
        f"/api/analysis-runs/{run['id']}/narrative/components/plot/retry"
    )
    assert second_retry.status_code == 202
    with client.app.state.session_factory() as session:
        plot_retry_claim = claim_next_task(
            session, worker_id="component-retry-plot-worker", lease_seconds=60
        )
    assert plot_retry_claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        plot_retry_claim,
        registry,
    )
    outdated = client.get(f"/api/analysis-runs/{run['id']}/workbench").json()
    assert outdated["deep_status"] == "OUTDATED"
    restart_deep = client.post(f"/api/analysis-runs/{run['id']}/deep/start")
    assert restart_deep.status_code == 202
    with client.app.state.session_factory() as session:
        pending_deep = list(session.scalars(
            select(Task)
            .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
            .where(
                AnalysisRunTask.run_id == run["id"],
                Task.kind == "analysis.deep_insights",
                Task.status == TaskStatus.PENDING.value,
            )
        ))
    assert len(pending_deep) == 1


def test_internal_ids_in_narrative_prose_are_rejected_before_publish(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-test"})
    run = client.post(
        f"/api/source-versions/{imported['version']['id']}/analysis/entities-events/start"
    ).json()
    registry = ProviderRegistry([InternalIdLeakProvider()])
    with client.app.state.session_factory() as session:
        foundation_claim = claim_next_task(
            session, worker_id="id-leak-foundation", lease_seconds=60
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
            session, worker_id="id-leak-overview", lease_seconds=60
        )
    assert narrative_claim is not None
    assert json.loads(narrative_claim.payload_json)["narrative_component"] == "overview"
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        narrative_claim,
        registry,
    )
    with client.app.state.session_factory() as session:
        task = session.get(Task, narrative_claim.id)
        assert task is not None
        assert task.status == TaskStatus.RETRY_WAIT.value
        assert "内部证据编号" in (task.last_error_message or "")
        assert session.scalar(
            select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run["id"])
        ) is None


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
        openai_base_url="http://127.0.0.1:18081/v1",
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


def test_openai_safe_json_repair_avoids_retry(tmp_path: Path) -> None:
    output_text = '结果如下：\n```json\n{"entities": [], "events": [],}\n```'
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
    assert result.parameters["json_repairs"] == [
        "extract_markdown_code_block",
        "remove_trailing_commas",
    ]


def test_openai_invalid_json_exposes_safe_parse_diagnostics(tmp_path: Path) -> None:
    output_text = '{"entities": [], "events": ['
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

    with pytest.raises(ProviderError) as caught:
        _run_provider(provider)

    assert caught.value.code == "PROVIDER_INVALID_OUTPUT"
    assert caught.value.raw_text == output_text
    assert caught.value.diagnostics["phase"] == "json_decode"
    assert caught.value.diagnostics["classification"] == "likely_truncated_output"
    assert "raw_output_sha256" in caught.value.diagnostics
    assert "raw_text" not in caught.value.diagnostics


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
        base_url="http://127.0.0.1:18082/v1",
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
        base_url="http://127.0.0.1:18082/v1",
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
