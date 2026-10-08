from __future__ import annotations

from collections import Counter
from typing import Any

from ..base import BaseQuestionPlugin
from ..contracts import LEARNING_QUESTION_ITEM_CONTRACTS
from ..schemas import (
    LearningAnswerProposal,
    LearningContractClassificationProposal,
    LearningContractItemProposal,
    LearningMetricProposal,
    LearningReportValidationError,
)

def _balanced_items(items: list[dict], chapter_key: str) -> list[dict]:
    """Order chapter material so every prefix is spread across the book."""
    ordered = sorted(items, key=lambda item: int(item.get(chapter_key) or 0))
    if len(ordered) <= 2:
        return ordered
    remaining = list(range(len(ordered)))
    selected_indexes: list[int] = []
    while remaining:
        if not selected_indexes:
            chosen = remaining[0]
        elif len(selected_indexes) == 1:
            chosen = remaining[-1]
        else:
            chosen = max(
                remaining,
                key=lambda index: min(abs(index - selected) for selected in selected_indexes),
            )
        selected_indexes.append(chosen)
        remaining.remove(chosen)
    return [ordered[index] for index in selected_indexes]



def _opening_action_counts(projection: dict) -> list[dict[str, object]]:
    events = projection.get("events", [])
    result: list[dict[str, object]] = []
    for chapter_limit in (3, 10, 30):
        people: dict[str, set[str]] = {}
        event_ids: set[str] = set()
        evidence_ids: set[str] = set()
        for event in events:
            chapters = {
                int(chapter)
                for chapter in event.get("chapter_ordinals", [])
                if int(chapter) > 0
            }
            if not any(chapter <= chapter_limit for chapter in chapters):
                continue
            event_id = str(event.get("id") or "")
            if event_id:
                event_ids.add(event_id)
            event_evidence = {
                str(evidence_id)
                for evidence_id in event.get("evidence_ids", [])
                if evidence_id
            }
            evidence_ids.update(event_evidence)
            for person in event.get("people", []):
                name = str(person).strip()
                if name:
                    people.setdefault(name, set()).update(event_evidence)
        result.append({
            "through_chapter": chapter_limit,
            "active_character_count": len(people),
            "active_characters": sorted(people),
            "event_count": len(event_ids),
            "evidence_ids": sorted(evidence_ids),
            "method": "按前 N 章事件中实际参与行动的具名人物去重；仅在人物名单中出现的不计入。",
        })
    return result


def _normalized_person_name(value: object) -> str:
    return "".join(str(value or "").split()).casefold()


def _opening_character_program_artifact(projection: dict) -> dict[str, object]:
    """Build the deterministic half of question 2.1 from canonical events.

    The model only proposes each first scene's narrative function. Sequence,
    counts, intervals and later activity volume remain program-owned facts.
    """

    character_profiles = {
        _normalized_person_name(item.get("name")): item
        for item in projection.get("characters", [])
        if isinstance(item, dict) and item.get("name")
    }
    role_labels = {
        "PROTAGONIST": "主角",
        "CORE_SUPPORTING": "核心配角",
        "IMPORTANT_SUPPORTING": "重要配角",
        "MINOR": "次要人物",
        "UNCLASSIFIED": "身份待进一步判断",
    }

    opening_events: list[tuple[int, int, dict]] = []
    for event_order, event in enumerate(projection.get("events", [])):
        chapter_ordinals = sorted({
            int(chapter)
            for chapter in event.get("chapter_ordinals", [])
            if 0 < int(chapter) <= 30
        })
        if not chapter_ordinals:
            continue
        opening_events.append((chapter_ordinals[0], event_order, event))
    opening_events.sort(key=lambda item: (item[0], item[1], str(item[2].get("id") or "")))

    people: dict[str, dict[str, object]] = {}
    canonical_labels: dict[str, str] = {}
    for first_chapter, event_order, event in opening_events:
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        event_evidence_ids = sorted({
            str(evidence_id)
            for evidence_id in event.get("evidence_ids", [])
            if evidence_id
        })
        for raw_name in event.get("people", []):
            name = str(raw_name or "").strip()
            normalized = _normalized_person_name(name)
            if not normalized:
                continue
            canonical_labels.setdefault(normalized, name)
            item = people.setdefault(normalized, {
                "character_name": canonical_labels[normalized],
                "first_action_chapter": first_chapter,
                "first_action_event_id": event_id,
                "first_action_event_title": str(event.get("title") or ""),
                "first_action_event_order": event_order,
                "first_action_evidence_ids": event_evidence_ids,
                "action_event_ids_through_30": [],
            })
            action_event_ids = item["action_event_ids_through_30"]
            if isinstance(action_event_ids, list) and event_id not in action_event_ids:
                action_event_ids.append(event_id)

    roles: list[dict[str, object]] = []
    for normalized_name, item in people.items():
        event_count = len(item["action_event_ids_through_30"])
        if event_count <= 1:
            volume = "单次行动"
        elif event_count <= 4:
            volume = "持续参与"
        else:
            volume = "高频参与"
        profile = character_profiles.get(normalized_name, {})
        identity_parts = [
            role_labels.get(str(profile.get("role") or ""), ""),
            "、".join(
                str(value).strip()
                for value in profile.get("identities", [])[:3]
                if str(value).strip()
            ),
            str(profile.get("description") or "").strip(),
        ]
        identity_summary = "；".join(
            dict.fromkeys(part for part in identity_parts if part)
        )
        roles.append({
            **item,
            "action_event_count_through_30": event_count,
            "later_role_volume": volume,
            "identity_summary": (
                identity_summary[:360]
                if identity_summary
                else "现有资料尚未形成可靠身份说明"
            ),
        })
    roles.sort(key=lambda item: (
        int(item["first_action_chapter"]),
        int(item["first_action_event_order"]),
        str(item["character_name"]),
    ))
    for sequence_no, item in enumerate(roles, start=1):
        item["sequence_no"] = sequence_no

    introductions_by_chapter: dict[int, list[str]] = {}
    for item in roles:
        introductions_by_chapter.setdefault(
            int(item["first_action_chapter"]),
            [],
        ).append(str(item["character_name"]))
    introduction_points: list[dict[str, object]] = []
    previous_chapter: int | None = None
    for chapter in sorted(introductions_by_chapter):
        introduction_points.append({
            "chapter_ordinal": chapter,
            "new_character_count": len(introductions_by_chapter[chapter]),
            "new_characters": introductions_by_chapter[chapter],
            "chapters_since_previous_introduction": (
                None if previous_chapter is None else chapter - previous_chapter
            ),
        })
        previous_chapter = chapter

    active_names = {
        _normalized_person_name(item["character_name"]) for item in roles
    }
    identity_candidates = [
        {
            key: candidate.get(key)
            for key in (
                "candidate_key",
                "left_name",
                "right_name",
                "reason",
                "evidence_ids",
            )
        }
        for candidate in projection.get("person_identity_candidates", [])
        if (
            _normalized_person_name(candidate.get("left_name")) in active_names
            or _normalized_person_name(candidate.get("right_name")) in active_names
        )
    ]
    return {
        "scope": "前 30 章内事件中实际参与行动的具名人物",
        "roles": roles,
        "introduction_points": introduction_points,
        "program_counts": _opening_action_counts(projection),
        "identity_duplicate_candidates": identity_candidates,
    }



def _validated_2_1_program_artifact(
    answer: LearningAnswerProposal,
    projection: dict,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    program_artifact = _opening_character_program_artifact(projection)
    roles = [
        item
        for item in program_artifact["roles"]
        if isinstance(item, dict)
    ]
    roles_by_sequence = {
        int(item["sequence_no"]): item
        for item in roles
    }
    function_item = next(
        (
            item
            for item in answer.contract_items
            if item.item_id == "first_scene_functions"
        ),
        None,
    )
    if function_item is None:
        raise ValueError("LEARNING_REPORT_2_1_FIRST_FUNCTIONS_MISSING")
    classifications_by_sequence: dict[
        int, LearningContractClassificationProposal
    ] = {}
    for classification in function_item.classifications:
        sequence_no = classification.sequence_no
        if sequence_no in classifications_by_sequence:
            raise ValueError("LEARNING_REPORT_2_1_CHARACTER_DUPLICATED")
        role = roles_by_sequence.get(sequence_no)
        if role is None:
            raise ValueError("LEARNING_REPORT_2_1_CHARACTER_REFERENCE_INVALID")
        classifications_by_sequence[sequence_no] = classification
    if set(classifications_by_sequence) != set(roles_by_sequence):
        missing = sorted(
            str(roles_by_sequence[sequence]["character_name"])
            for sequence in set(roles_by_sequence) - set(classifications_by_sequence)
        )
        raise ValueError(
            "LEARNING_REPORT_2_1_CHARACTER_COVERAGE_INVALID:"
            + "、".join(missing[:20])
        )

    function_distribution: dict[str, int] = {}
    completed_roles: list[dict[str, object]] = []
    for role in roles:
        classification = classifications_by_sequence[int(role["sequence_no"])]
        function_distribution[classification.category] = (
            function_distribution.get(classification.category, 0) + 1
        )
        completed_roles.append({
            key: value
            for key, value in role.items()
            if key != "first_action_event_order"
        } | {
            "first_scene_function": classification.category,
            "first_scene_function_evidence_ids": list(
                role["first_action_evidence_ids"]
            ),
        })
    completed_artifact = {
        **program_artifact,
        "roles": completed_roles,
        "function_distribution": [
            {"function": function, "count": count}
            for function, count in sorted(
                function_distribution.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "contract_validation": {
            "required_item_count": len(
                LEARNING_QUESTION_ITEM_CONTRACTS["2.1"]
            ),
            "covered_item_count": len(
                LEARNING_QUESTION_ITEM_CONTRACTS["2.1"]
            ),
            "model_returned_item_count": len(answer.contract_items),
            "character_classification_complete": True,
            "program_owned_fields": [
                "人物计数",
                "首行动顺序",
                "后续行动事件数与量级",
                "新增人物间隔",
                "功能分布",
                "身份重复候选",
            ],
            "model_proposed_program_validated_fields": ["首场功能"],
        },
    }
    program_metrics = [
        {
            "label": f"前 {item['through_chapter']} 章有效行动人物",
            "value": str(item["active_character_count"]),
            "unit": "人",
            "method": str(item["method"]),
            "evidence_ids": list(item.get("evidence_ids", []))[:16],
        }
        for item in program_artifact["program_counts"]
    ]
    program_metrics.extend([
        {
            "label": "首次行动引入批次",
            "value": str(len(program_artifact["introduction_points"])),
            "unit": "批",
            "method": "按人物首次参与有效行动的章节去重后计数。",
            "evidence_ids": [],
        },
        {
            "label": "未裁定身份重复候选",
            "value": str(len(program_artifact["identity_duplicate_candidates"])),
            "unit": "组",
            "method": "仅统计涉及前 30 章有效行动人物、且尚未由程序或用户裁定的身份候选。",
            "evidence_ids": [],
        },
    ])
    return completed_artifact, program_metrics


def _program_2_1_contract_items(
    artifact: dict[str, object],
) -> list[dict[str, object]]:
    counts = {
        int(item["through_chapter"]): int(item["active_character_count"])
        for item in artifact["program_counts"]
        if isinstance(item, dict)
    }
    function_distribution = [
        item
        for item in artifact["function_distribution"]
        if isinstance(item, dict)
    ]
    top_functions = "、".join(
        f"{item['function']} {item['count']} 人"
        for item in function_distribution[:3]
    )
    roles = [
        item for item in artifact["roles"] if isinstance(item, dict)
    ]
    introduction_points = [
        item
        for item in artifact["introduction_points"]
        if isinstance(item, dict)
    ]
    identity_candidates = [
        item
        for item in artifact["identity_duplicate_candidates"]
        if isinstance(item, dict)
    ]
    volume_counts = Counter(
        str(item.get("later_role_volume") or "")
        for item in roles
        if item.get("later_role_volume")
    )
    item_payloads = {
        "opening_character_counts": {
            "status": "SUPPORTED",
            "finding": (
                f"程序计数：前 3、10、30 章分别为 "
                f"{counts.get(3, 0)}、{counts.get(10, 0)}、"
                f"{counts.get(30, 0)} 人。"
            ),
        },
        "character_appearance_sequence": {
            "status": "SUPPORTED",
            "finding": f"完整记录 {len(roles)} 人的首次有效行动顺序、章节与事件。",
        },
        "first_scene_functions": {
            "status": "SUPPORTED",
            "finding": (
                f"模型只提交逐人功能类别，程序按序号合并并核对覆盖；"
                f"数量前三为{top_functions or '暂无'}。"
            ),
        },
        "later_role_volume": {
            "status": "SUPPORTED",
            "finding": "；".join(
                f"{label} {volume_counts[label]} 人"
                for label in ("高频参与", "持续参与", "单次行动")
                if volume_counts[label]
            ) or "当前没有可分档人物。",
        },
        "new_character_intervals": {
            "status": "SUPPORTED",
            "finding": (
                f"程序按首次有效行动章节识别出 {len(introduction_points)} 个引入批次。"
            ),
        },
        "first_function_distribution": {
            "status": "SUPPORTED",
            "finding": f"程序由完整逐人分类汇总；数量前三为{top_functions or '暂无'}。",
        },
        "identity_duplicate_risks": {
            "status": "SUPPORTED",
            "finding": f"单列 {len(identity_candidates)} 组尚未裁定的身份重复候选。",
        },
        "cross_book_comparison": {
            "status": "INSUFFICIENT_EVIDENCE",
            "finding": "缺少同品类、同商业模式作品的同口径数据，不能形成开篇标配阵容。",
        },
    }
    return [
        {
            "item_id": definition.item_id,
            **item_payloads[definition.item_id],
            "metrics": [],
            "evidence_ids": [],
            "limitations": (
                ["当前只能形成单书观察，不能外推为品类标准。"]
                if definition.item_id == "cross_book_comparison"
                else []
            ),
            "classifications": [],
        }
        for definition in LEARNING_QUESTION_ITEM_CONTRACTS["2.1"]
    ]


def _program_2_1_representative_evidence_ids(
    artifact: dict[str, object],
) -> list[str]:
    roles = [
        item for item in artifact["roles"] if isinstance(item, dict)
    ]
    selected: list[str] = []
    seen: set[str] = set()
    for role in _balanced_items(roles, "first_action_chapter"):
        for evidence_id in role.get("first_action_evidence_ids", []):
            value = str(evidence_id)
            if value and value not in seen:
                selected.append(value)
                seen.add(value)
                break
        if len(selected) >= 24:
            break
    return selected


def _program_2_1_reading_fields(
    artifact: dict[str, object],
) -> dict[str, list[str] | str]:
    counts = {
        int(item["through_chapter"]): int(item["active_character_count"])
        for item in artifact["program_counts"]
        if isinstance(item, dict)
    }
    introduction_count = len(artifact["introduction_points"])
    function_distribution = [
        item
        for item in artifact["function_distribution"]
        if isinstance(item, dict)
    ]
    top_functions = "、".join(
        f"{item['function']} {item['count']} 人"
        for item in function_distribution[:3]
    )
    volume_counts: dict[str, int] = {}
    for role in artifact["roles"]:
        if not isinstance(role, dict):
            continue
        volume = str(role.get("later_role_volume") or "")
        if volume:
            volume_counts[volume] = volume_counts.get(volume, 0) + 1
    volume_summary = "、".join(
        f"{label} {volume_counts[label]} 人"
        for label in ("高频参与", "持续参与", "单次行动")
        if volume_counts.get(label)
    )
    identity_candidate_count = len(artifact["identity_duplicate_candidates"])
    representative_roles = sorted(
        (
            item
            for item in artifact["roles"]
            if isinstance(item, dict)
        ),
        key=lambda item: (
            -int(item.get("action_event_count_through_30") or 0),
            int(item.get("sequence_no") or 0),
        ),
    )[:8]
    representative_summary = "；".join(
        (
            f"{item.get('character_name')}（{item.get('identity_summary')}）"
            f"在第 {item.get('first_action_chapter')} 章以"
            f"“{item.get('first_scene_function')}”功能进入，"
            f"前 30 章参与 {item.get('action_event_count_through_30')} 个行动事件"
        )
        for item in representative_roles
    )
    conclusion = (
        f"前 3、10、30 章分别有 {counts.get(3, 0)}、"
        f"{counts.get(10, 0)}、{counts.get(30, 0)} 名具名人物参与有效行动；"
        f"前 30 章共有 {introduction_count} 个首次行动引入批次。"
        "这些数字只说明人物进入速度，必须和“谁进入、以什么身份、"
        "承担什么功能、后续是否继续参与”一起看。"
        f"高参与度代表人物为：{representative_summary or '暂无'}。"
        f"经逐人首次事件与证据核对，首场功能分类数量前三为"
        f"{top_functions or '暂无'}；后续行动量级为{volume_summary or '暂无'}。"
        f"当前仍有 {identity_candidate_count} 组身份重复候选未裁定，"
        "且没有同口径跨书数据，因此本问只能作为单书部分回答。"
    )
    limitations = [
        (
            f"仍有 {identity_candidate_count} 组涉及前 30 章人物的身份重复候选"
            "尚未裁定；程序不会静默合并，当前人数可能偏高。"
        ),
        "缺少同品类、同商业模式作品的同口径数据，不能把本书人物规模当成品类标准。",
    ]
    reusable_lessons = [
        (
            f"本书不是一次性罗列人物，而是在前 30 章的 "
            f"{introduction_count} 个行动批次中持续引入；设计群像时可借鉴"
            "“随事件入场”，但人物数量应按自己的篇幅和识别负担决定。"
        ),
        (
            f"首场功能数量前三为{top_functions or '暂无'}。"
            "可借鉴“首次行动即承担明确叙事功能”的做法，"
            "不能照搬本书的类别比例。"
        ),
        (
            "人物数量本身不构成方法；真正可参考的是身份、首场功能与"
            "后续参与量如何配合，让人物随事件进入并继续产生作用。"
        ),
    ]
    do_not_copy = [
        (
            f"不要把前 30 章 {counts.get(30, 0)} 名有效行动人物的密度"
            "直接当成开书标准；没有稳定识别锚点时会增加读者记忆负担。"
        ),
        (
            f"在 {identity_candidate_count} 组身份重复候选尚未裁定、"
            "又缺少跨书对照时，不要把当前人数或功能比例外推为市场规律。"
        ),
    ]
    return {
        "conclusion": conclusion,
        "limitations": limitations,
        "reusable_lessons": reusable_lessons,
        "do_not_copy": do_not_copy,
    }



class Q2_1Plugin(BaseQuestionPlugin):
    question_id = "2.1"
    requires_projection = True

    def assess_readiness(self, projection: dict[str, Any]) -> dict[str, object]:
        chapter_count = int(projection.get("chapter_count") or 0)
        opening_character_artifact = _opening_character_program_artifact(projection)
        action_counts = opening_character_artifact["program_counts"]
        opening_character_roles = opening_character_artifact["roles"]
        action_gaps: list[str] = []
        for item in action_counts:
            if min(int(item["through_chapter"]), chapter_count) > 0 and int(item["active_character_count"]) == 0:
                action_gaps.append(
                    f"前 {item['through_chapter']} 章没有可由事件证据确认的有效行动人物。"
                )
        roles_without_first_evidence = [
            str(item["character_name"])
            for item in opening_character_roles
            if not item.get("first_action_evidence_ids")
        ]
        action_ready = bool(opening_character_roles) and not roles_without_first_evidence
        if not action_ready:
            action_gaps.append("现有章节没有可由事件原文确认的有效行动人物。")
        if roles_without_first_evidence:
            action_gaps.append(
                f"有 {len(roles_without_first_evidence)} 位人物的首次行动事件缺少原文，"
                "不能判断首场功能。"
            )
        action_limitations = list(action_gaps)
        if chapter_count < 30:
            action_limitations.append(
                f"当前只有 {chapter_count} 章，只能按实际篇幅回答，不能完成前 30 章口径。"
            )
        identity_candidate_count = len(
            opening_character_artifact["identity_duplicate_candidates"]
        )
        if identity_candidate_count:
            action_limitations.append(
                f"仍有 {identity_candidate_count} 组涉及开篇人物的身份候选未裁定，"
                "程序会单列风险，不把它们静默合并。"
            )
        action_limitations.append("缺少同品类同商业模式作品的开篇阵容对照。")

        return {
            "question_id": self.question_id,
            "ready": action_ready,
            "observed": {
                "program_counts": action_counts,
                "opening_character_role_count": len(opening_character_roles),
                "introduction_point_count": len(
                    opening_character_artifact["introduction_points"]
                ),
                "identity_duplicate_candidate_count": identity_candidate_count,
                "roles_without_first_evidence_count": len(roles_without_first_evidence),
            },
            "gaps": action_limitations,
            "required_artifact": (
                "2.1 角色首行动序、首场功能、后续量级、新增间隔、"
                "功能分布与身份风险完整账本"
            ),
            "answer_scope": "PARTIAL" if action_ready else "NOT_READY",
            "source_material": opening_character_artifact,
        }

    def validate_answer(
        self,
        answer: LearningAnswerProposal,
        projection: dict[str, Any],
        errors: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        item_by_id = {item.item_id: item for item in answer.contract_items}
        function_item = item_by_id.get("first_scene_functions")
        if (
            function_item is None
            or function_item.status != "SUPPORTED"
            or not function_item.classifications
        ):
            raise LearningReportValidationError(
                "LEARNING_REPORT_2_1_FIRST_FUNCTIONS_MISSING",
                [{
                    "path": [
                        "answers",
                        answer.question_id,
                        "contract_items",
                        "first_scene_functions",
                    ],
                    "type": "value_error",
                    "message": "2.1 必须逐人返回首场功能分类，不能只给人数。",
                }],
            )
        _validated_2_1_program_artifact(answer, projection)

    def apply_program_answer(
        self,
        report: Any,
        projection: dict[str, Any],
    ) -> LearningAnswerProposal | None:
        ans = next((a for a in report.answers if a.question_id == "2.1"), None)
        if ans:
            artifact, program_metrics = _validated_2_1_program_artifact(ans, projection)
            ans.contract_items = [
                LearningContractItemProposal.model_validate(item)
                for item in _program_2_1_contract_items(artifact)
            ]
            ans.evidence_ids = _program_2_1_representative_evidence_ids(artifact)
            ans.metrics = program_metrics
            reading = _program_2_1_reading_fields(artifact)
            ans.conclusion = reading["conclusion"]
            ans.limitations = reading["limitations"]
            ans.reusable_lessons = reading["reusable_lessons"]
            ans.do_not_copy = reading["do_not_copy"]
        return ans
