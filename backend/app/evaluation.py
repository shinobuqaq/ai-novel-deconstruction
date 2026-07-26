from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


SCHEMA_VERSION = "1.0"
DEFAULT_SAMPLE_CHARS = 50_000
VALID_REVIEW_STATUSES = {"PENDING", "CONFIRMED", "SECOND_REVIEWED"}
PHENOMENON_LABELS = {
    "ALIAS": "别名",
    "SAME_NAME": "同名人物",
    "CROSS_CHAPTER": "跨章事件",
    "FACT_INVALIDATION": "事实失效",
}


class EvaluationError(RuntimeError):
    pass


def _normalize_name(value: str) -> str:
    return re.sub(r"[【】\[\]（）()\s，。、“”‘’：:!?！？]", "", value).casefold()


def _pair_key(left_name: str, right_name: str) -> tuple[str, str]:
    return tuple(sorted((_normalize_name(left_name), _normalize_name(right_name))))  # type: ignore[return-value]


def _overlaps(start: int, end: int, sample_start: int, sample_end: int) -> bool:
    return start < sample_end and end > sample_start


def _metric(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def _http_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise EvaluationError(f"接口返回 {error.code}：{url}\n{detail}") from error
    except URLError as error:
        raise EvaluationError(f"无法连接工作台接口：{url}\n{error.reason}") from error


def fetch_workbench(api_base: str, run_id: str) -> dict[str, Any]:
    return _http_json(
        f"{api_base.rstrip('/')}/api/analysis-runs/{quote(run_id)}/workbench"
    )


def fetch_evidence_index(
    api_base: str,
    evidence_ids: set[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, evidence_id in enumerate(sorted(evidence_ids), start=1):
        try:
            payload = _http_json(
                f"{api_base.rstrip('/')}/api/evidence/{quote(evidence_id)}"
            )
        except EvaluationError as error:
            raise EvaluationError(
                f"读取第 {index}/{len(evidence_ids)} 条原文依据失败：{evidence_id}\n{error}"
            ) from error
        evidence = payload.get("evidence") or {}
        result[evidence_id] = {
            "id": evidence_id,
            "chapter_title": payload.get("chapter_title", ""),
            "start_char": int(evidence.get("start_char") or 0),
            "end_char": int(evidence.get("end_char") or 0),
            "text_snapshot": evidence.get("text_snapshot", ""),
            "context_text": payload.get("context_text", ""),
        }
    return result


def required_evidence_ids(workbench: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for candidate in workbench.get("person_identity_candidates", []):
        ids.update(candidate.get("evidence_ids", []))
    for event in workbench.get("events", []):
        ids.update(event.get("evidence_ids", []))
    deep = workbench.get("deep_analysis") or {}
    for fact in deep.get("fact_versions", []):
        ids.update(fact.get("evidence_ids", []))
        ids.update(fact.get("counter_evidence_ids", []))
    decided_names = {
        _normalize_name(str(name))
        for decision in workbench.get("person_identity_decisions", [])
        for name in (decision.get("left_name"), decision.get("right_name"))
        if name
    }
    for character in workbench.get("characters", []):
        character_names = {
            _normalize_name(str(name))
            for name in [character.get("name", ""), *character.get("aliases", [])]
            if name
        }
        if character_names & decided_names:
            ids.update(character.get("evidence_ids", [])[:4])
    return {str(item) for item in ids if item}


def _person_evidence_ids(
    workbench: dict[str, Any],
    *names: str,
) -> list[str]:
    normalized_names = {_normalize_name(name) for name in names if name}
    result: list[str] = []
    for character in workbench.get("characters", []):
        character_names = {
            _normalize_name(str(name))
            for name in [character.get("name", ""), *character.get("aliases", [])]
            if name
        }
        if not character_names & normalized_names:
            continue
        for evidence_id in character.get("evidence_ids", []):
            if evidence_id not in result:
                result.append(evidence_id)
    return result[:4]


def _item_evidence_positions(
    item: dict[str, Any],
    evidence_index: dict[str, dict[str, Any]],
) -> list[int]:
    return [
        int(evidence_index[evidence_id]["start_char"])
        for evidence_id in item.get("evidence_ids", [])
        if evidence_id in evidence_index
    ]


def _identity_sample_items(workbench: dict[str, Any]) -> list[dict[str, Any]]:
    items = list(workbench.get("person_identity_candidates", []))
    known_pairs = {
        _pair_key(item["left_name"], item["right_name"])
        for item in items
    }
    for decision in workbench.get("person_identity_decisions", []):
        pair_key = _pair_key(decision["left_name"], decision["right_name"])
        if pair_key in known_pairs:
            continue
        items.append({
            "left_name": decision["left_name"],
            "right_name": decision["right_name"],
            "review_priority": "BLOCKING",
            "cooccurrence_count": 0,
            "evidence_ids": _person_evidence_ids(
                workbench,
                decision["left_name"],
                decision["right_name"],
            ),
        })
        known_pairs.add(pair_key)
    return items


def choose_sample_window(
    workbench: dict[str, Any],
    evidence_index: dict[str, dict[str, Any]],
    sample_chars: int = DEFAULT_SAMPLE_CHARS,
) -> tuple[int, int, dict[str, int]]:
    if sample_chars <= 0:
        raise EvaluationError("样本字符数必须大于 0。")
    candidates = _identity_sample_items(workbench)
    events = workbench.get("events", [])
    facts = (workbench.get("deep_analysis") or {}).get("fact_versions", [])
    anchors = {0}
    for item in [*candidates, *facts]:
        for position in _item_evidence_positions(item, evidence_index):
            anchors.add(max(0, position))
            anchors.add(max(0, position - sample_chars + 1))
    for event in events:
        start = int(event.get("start_char") or 0)
        anchors.add(max(0, start))
        anchors.add(max(0, start - sample_chars + 1))

    def score(sample_start: int) -> tuple[int, dict[str, int]]:
        sample_end = sample_start + sample_chars
        selected_candidates = [
            item
            for item in candidates
            if any(
                sample_start <= position < sample_end
                for position in _item_evidence_positions(item, evidence_index)
            )
        ]
        selected_events = [
            item
            for item in events
            if _overlaps(
                int(item.get("start_char") or 0),
                int(item.get("end_char") or 0),
                sample_start,
                sample_end,
            )
        ]
        selected_facts = [
            item
            for item in facts
            if any(
                sample_start <= position < sample_end
                for position in _item_evidence_positions(item, evidence_index)
            )
        ]
        blocking = sum(
            item.get("review_priority") == "BLOCKING"
            for item in selected_candidates
        )
        hard_negatives = sum(
            int(item.get("cooccurrence_count") or 0) > 0
            for item in selected_candidates
        )
        cross_chapter = sum(
            len(item.get("chapter_ordinals", [])) > 1
            for item in selected_events
        )
        temporal_facts = sum(
            item.get("valid_to_chapter") is not None
            or item.get("timeline_status") not in {None, "", "ACTIVE"}
            for item in selected_facts
        )
        coverage = {
            "person_pairs": len(selected_candidates),
            "blocking_person_pairs": blocking,
            "hard_negative_pairs": hard_negatives,
            "events": len(selected_events),
            "cross_chapter_events": cross_chapter,
            "facts": len(selected_facts),
            "temporal_facts": temporal_facts,
        }
        required_phenomena = sum((
            len(selected_candidates) > 0,
            hard_negatives > 0,
            cross_chapter > 0,
            len(selected_facts) > 0,
            temporal_facts > 0,
        ))
        weighted = (
            required_phenomena * 1_000_000
            + len(selected_candidates) * 20
            + blocking * 10
            + hard_negatives * 25
            + len(selected_events)
            + cross_chapter * 10
            + len(selected_facts) * 8
            + temporal_facts * 15
        )
        return weighted, coverage

    best_start = 0
    best_score = -1
    best_coverage: dict[str, int] = {}
    for anchor in sorted(anchors):
        current_score, coverage = score(anchor)
        if current_score > best_score:
            best_score = current_score
            best_start = anchor
            best_coverage = coverage
    return best_start, best_start + sample_chars, best_coverage


def _evidence_refs(
    evidence_ids: list[str],
    evidence_index: dict[str, dict[str, Any]],
    sample_start: int,
    sample_end: int,
) -> list[dict[str, Any]]:
    return [
        evidence_index[evidence_id]
        for evidence_id in evidence_ids
        if evidence_id in evidence_index
        and _overlaps(
            int(evidence_index[evidence_id]["start_char"]),
            int(evidence_index[evidence_id]["end_char"]),
            sample_start,
            sample_end,
        )
    ]


def build_gold_template(
    workbench: dict[str, Any],
    evidence_index: dict[str, dict[str, Any]],
    *,
    sample_start: int | None = None,
    sample_chars: int = DEFAULT_SAMPLE_CHARS,
) -> dict[str, Any]:
    auto_coverage: dict[str, int] = {}
    if sample_start is None:
        sample_start, sample_end, auto_coverage = choose_sample_window(
            workbench,
            evidence_index,
            sample_chars,
        )
    else:
        if sample_start < 0:
            raise EvaluationError("样本起始字符不能小于 0。")
        sample_end = sample_start + sample_chars

    selected_candidates = []
    selected_pair_keys: set[tuple[str, str]] = set()
    for candidate in workbench.get("person_identity_candidates", []):
        evidence = _evidence_refs(
            candidate.get("evidence_ids", []),
            evidence_index,
            sample_start,
            sample_end,
        )
        if not evidence:
            continue
        selected_pair_keys.add(
            _pair_key(candidate["left_name"], candidate["right_name"])
        )
        selected_candidates.append({
            "gold_id": candidate["candidate_key"],
            "left_name": candidate["left_name"],
            "right_name": candidate["right_name"],
            "system_candidate_key": candidate["candidate_key"],
            "system_recommendation": candidate.get("recommended_decision", ""),
            "system_reason": candidate.get("reason", ""),
            "risk": (
                "HIGH"
                if candidate.get("review_priority") == "BLOCKING"
                else "NORMAL"
            ),
            "phenomena": [
                "ALIAS",
                *(
                    ["COOCCURRENCE_NEGATIVE"]
                    if int(candidate.get("cooccurrence_count") or 0) > 0
                    else []
                ),
            ],
            "gold_label": "PENDING",
            "review_status": "PENDING",
            "second_review_status": "NOT_SELECTED",
            "review_note": "",
            "evidence": evidence,
        })
    for decision in workbench.get("person_identity_decisions", []):
        pair_key = _pair_key(decision["left_name"], decision["right_name"])
        if pair_key in selected_pair_keys:
            continue
        evidence = _evidence_refs(
            _person_evidence_ids(
                workbench,
                decision["left_name"],
                decision["right_name"],
            ),
            evidence_index,
            sample_start,
            sample_end,
        )
        if not evidence:
            continue
        selected_pair_keys.add(pair_key)
        selected_candidates.append({
            "gold_id": decision["candidate_key"],
            "left_name": decision["left_name"],
            "right_name": decision["right_name"],
            "system_candidate_key": decision["candidate_key"],
            "system_recommendation": decision["decision"],
            "system_reason": "用户已在工作台完成身份裁决。",
            "risk": "HIGH",
            "phenomena": ["ALIAS"],
            "gold_label": decision["decision"],
            "review_status": "CONFIRMED",
            "second_review_status": "NOT_SELECTED",
            "review_note": "由工作台中的用户裁决导入。",
            "evidence": evidence,
        })

    selected_events = []
    for index, event in enumerate(workbench.get("events", []), start=1):
        if not _overlaps(
            int(event.get("start_char") or 0),
            int(event.get("end_char") or 0),
            sample_start,
            sample_end,
        ):
            continue
        selected_events.append({
            "gold_id": f"gold_event_{index:04d}",
            "source_event_id": event["id"],
            "title": event["title"],
            "chapter_ordinals": event.get("chapter_ordinals", []),
            "start_char": int(event.get("start_char") or 0),
            "end_char": int(event.get("end_char") or 0),
            "risk": "HIGH" if len(event.get("chapter_ordinals", [])) > 1 else "NORMAL",
            "phenomena": (
                ["CROSS_CHAPTER"]
                if len(event.get("chapter_ordinals", [])) > 1
                else []
            ),
            "review_status": "PENDING",
            "second_review_status": "NOT_SELECTED",
            "review_note": "",
            "evidence": _evidence_refs(
                event.get("evidence_ids", []),
                evidence_index,
                sample_start,
                sample_end,
            ),
        })

    selected_facts = []
    facts = (workbench.get("deep_analysis") or {}).get("fact_versions", [])
    for fact in facts:
        evidence = _evidence_refs(
            fact.get("evidence_ids", []),
            evidence_index,
            sample_start,
            sample_end,
        )
        if not evidence:
            continue
        selected_facts.append({
            "gold_id": fact["id"],
            "source_fact_id": fact["id"],
            "subject": fact["subject"],
            "predicate": fact["predicate"],
            "value": fact["value"],
            "valid_from_chapter": fact.get("valid_from_chapter"),
            "valid_to_chapter": fact.get("valid_to_chapter"),
            "gold_label": "PENDING",
            "risk": "HIGH",
            "phenomena": (
                ["FACT_INVALIDATION"]
                if fact.get("valid_to_chapter") is not None
                else []
            ),
            "review_status": "PENDING",
            "second_review_status": "NOT_SELECTED",
            "review_note": "",
            "evidence": evidence,
        })
    selected_facts = selected_facts[:30]

    if not auto_coverage:
        auto_coverage = {
            "person_pairs": len(selected_candidates),
            "blocking_person_pairs": sum(
                item["risk"] == "HIGH" for item in selected_candidates
            ),
            "hard_negative_pairs": sum(
                item.get("system_recommendation") == "DIFFERENT"
                for item in selected_candidates
            ),
            "events": len(selected_events),
            "cross_chapter_events": sum(
                len(item["chapter_ordinals"]) > 1 for item in selected_events
            ),
            "facts": len(selected_facts),
            "temporal_facts": sum(
                item.get("valid_to_chapter") is not None for item in selected_facts
            ),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "run_id": workbench["run_id"],
            "source_version_id": workbench["source_version_id"],
            "created_at": datetime.now(UTC).isoformat(),
            "annotation_status": "DRAFT",
            "sample_start_char": sample_start,
            "sample_end_char": sample_end,
            "sample_chars": sample_chars,
            "minimum_second_review_ratio": 0.20,
            "coverage_at_export": auto_coverage,
        },
        "instructions": [
            "系统输出只用于预标注，不能直接当成答案。",
            "人物对：把 gold_label（金标结论）改为 SAME（同一人物）或 DIFFERENT（不同人物），并把 review_status（复核状态）改为 CONFIRMED（已确认）；系统漏掉的人物对请追加，并用 phenomena（现象标签）标出 SAME_NAME（同名人物）等现象。",
            "事件：真实事件改为 CONFIRMED（已确认），可修正标题与边界；误报改为 REJECTED（已否决）；漏掉的事件请追加且 source_event_id（来源事件编号）留空，跨章事件标记 CROSS_CHAPTER（跨章事件）。",
            "事实：至少确认 20 条，把 gold_label（金标结论）改为 CORRECT（正确）、INCORRECT（错误）或 DISPUTED（有争议）；事实失效样本标记 FACT_INVALIDATION（事实失效）。",
            "高风险条目至少 20% 完成第二轮复核，并把 second_review_status（二次复核状态）改为 CONFIRMED（已确认）。",
            "全部标注完成后把 metadata.annotation_status（金标状态）改为 READY（可对拍）；正式对拍默认拒绝草稿。",
        ],
        "person_identity_pairs": selected_candidates,
        "gold_events": selected_events,
        "gold_facts": selected_facts,
    }


def validate_gold(gold: dict[str, Any], *, allow_draft: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    metadata = gold.get("metadata") or {}
    if gold.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"不支持的金标版本：{gold.get('schema_version')!r}")
    if not allow_draft and metadata.get("annotation_status") != "READY":
        errors.append("金标仍是草稿；完成标注后把 metadata.annotation_status 改为 READY。")

    people = gold.get("person_identity_pairs", [])
    for item in people:
        if item.get("review_status") not in VALID_REVIEW_STATUSES:
            errors.append(f"人物对 {item.get('gold_id')} 的 review_status 无效。")
        if item.get("review_status") != "PENDING" and item.get("gold_label") not in {
            "SAME",
            "DIFFERENT",
        }:
            errors.append(f"人物对 {item.get('gold_id')} 缺少 SAME/DIFFERENT 金标。")
    for item in gold.get("gold_events", []):
        if item.get("review_status") not in {
            "PENDING",
            "CONFIRMED",
            "SECOND_REVIEWED",
            "REJECTED",
        }:
            errors.append(f"事件 {item.get('gold_id')} 的 review_status 无效。")
    for item in gold.get("gold_facts", []):
        if item.get("review_status") not in VALID_REVIEW_STATUSES:
            errors.append(f"事实 {item.get('gold_id')} 的 review_status 无效。")
        if item.get("review_status") != "PENDING" and item.get("gold_label") not in {
            "CORRECT",
            "INCORRECT",
            "DISPUTED",
        }:
            errors.append(f"事实 {item.get('gold_id')} 缺少有效金标。")

    if not allow_draft:
        pending_count = sum(
            item.get("review_status") == "PENDING"
            for collection in (
                people,
                gold.get("gold_events", []),
                gold.get("gold_facts", []),
            )
            for item in collection
        )
        if pending_count:
            errors.append(f"仍有 {pending_count} 条预标注没有完成确认。")
        reviewed_facts = [
            item
            for item in gold.get("gold_facts", [])
            if item.get("review_status") in {"CONFIRMED", "SECOND_REVIEWED"}
        ]
        if len(reviewed_facts) < 20:
            errors.append(f"已确认核心事实只有 {len(reviewed_facts)} 条，至少需要 20 条。")
        confirmed_people = [
            item
            for item in people
            if item.get("review_status") in {"CONFIRMED", "SECOND_REVIEWED"}
        ]
        confirmed_events = [
            item
            for item in gold.get("gold_events", [])
            if item.get("review_status") in {"CONFIRMED", "SECOND_REVIEWED"}
        ]
        required_phenomena = {
            "ALIAS": any(
                "ALIAS" in item.get("phenomena", [])
                for item in confirmed_people
            ),
            "SAME_NAME": any(
                "SAME_NAME" in item.get("phenomena", [])
                for item in confirmed_people
            ),
            "CROSS_CHAPTER": any(
                "CROSS_CHAPTER" in item.get("phenomena", [])
                for item in confirmed_events
            ),
            "FACT_INVALIDATION": any(
                "FACT_INVALIDATION" in item.get("phenomena", [])
                for item in reviewed_facts
            ),
        }
        missing_phenomena = [
            name for name, covered in required_phenomena.items() if not covered
        ]
        if missing_phenomena:
            errors.append(
                "金标缺少必备现象："
                + "、".join(
                    PHENOMENON_LABELS.get(name, name)
                    for name in missing_phenomena
                )
                + "。"
            )

    high_risk = [
        item
        for collection in (
            people,
            gold.get("gold_events", []),
            gold.get("gold_facts", []),
        )
        for item in collection
        if item.get("risk") == "HIGH"
        and item.get("review_status") in {"CONFIRMED", "SECOND_REVIEWED"}
    ]
    second_reviewed = sum(
        item.get("second_review_status") == "CONFIRMED"
        or item.get("review_status") == "SECOND_REVIEWED"
        for item in high_risk
    )
    required_ratio = float(metadata.get("minimum_second_review_ratio") or 0.20)
    second_review_ratio = _metric(second_reviewed, len(high_risk))
    if high_risk and (
        second_review_ratio is None or second_review_ratio + 1e-12 < required_ratio
    ):
        errors.append(
            f"高风险条目二次复核不足：{second_reviewed}/{len(high_risk)}，"
            f"要求至少 {required_ratio:.0%}。"
        )
    return {
        "valid": not errors,
        "errors": errors,
        "high_risk_reviewed": len(high_risk),
        "high_risk_second_reviewed": second_reviewed,
        "second_review_ratio": second_review_ratio,
    }


def _character_owners(workbench: dict[str, Any]) -> dict[str, set[str]]:
    owners: dict[str, set[str]] = {}
    for character in workbench.get("characters", []):
        character_id = str(character["id"])
        for name in [character.get("name", ""), *character.get("aliases", [])]:
            normalized = _normalize_name(str(name))
            if normalized:
                owners.setdefault(normalized, set()).add(character_id)
    return owners


def score_gold(
    gold: dict[str, Any],
    workbench: dict[str, Any],
    *,
    allow_draft: bool = False,
) -> dict[str, Any]:
    validation = validate_gold(gold, allow_draft=allow_draft)
    if not validation["valid"]:
        raise EvaluationError("\n".join(validation["errors"]))
    if gold["metadata"].get("run_id") != workbench.get("run_id"):
        raise EvaluationError("金标 run_id 与当前工作台运行不一致，拒绝对拍。")

    owners = _character_owners(workbench)
    candidate_pairs = {
        _pair_key(item["left_name"], item["right_name"])
        for item in workbench.get("person_identity_candidates", [])
    }
    reviewed_people = [
        item
        for item in gold.get("person_identity_pairs", [])
        if item.get("review_status") in {"CONFIRMED", "SECOND_REVIEWED"}
    ]
    tp = fp = fn = tn = candidate_hits = 0
    for item in reviewed_people:
        left_owners = owners.get(_normalize_name(item["left_name"]), set())
        right_owners = owners.get(_normalize_name(item["right_name"]), set())
        predicted_same = bool(left_owners & right_owners)
        gold_same = item["gold_label"] == "SAME"
        if predicted_same and gold_same:
            tp += 1
        elif predicted_same:
            fp += 1
        elif gold_same:
            fn += 1
        else:
            tn += 1
        if gold_same and (
            predicted_same
            or _pair_key(item["left_name"], item["right_name"]) in candidate_pairs
        ):
            candidate_hits += 1
    precision = _metric(tp, tp + fp)
    recall = _metric(tp, tp + fn)
    gold_same_count = tp + fn

    system_events = workbench.get("events", [])
    confirmed_events = [
        item
        for item in gold.get("gold_events", [])
        if item.get("review_status") in {"CONFIRMED", "SECOND_REVIEWED"}
    ]
    event_hits = 0
    for gold_event in confirmed_events:
        source_event_id = gold_event.get("source_event_id")
        matched = any(
            (
                source_event_id
                and event.get("id") == source_event_id
            )
            or _overlaps(
                int(event.get("start_char") or 0),
                int(event.get("end_char") or 0),
                int(gold_event.get("start_char") or 0),
                int(gold_event.get("end_char") or 0),
            )
            for event in system_events
        )
        event_hits += matched

    reviewed_facts = [
        item
        for item in gold.get("gold_facts", [])
        if item.get("review_status") in {"CONFIRMED", "SECOND_REVIEWED"}
    ]
    decidable_facts = [
        item for item in reviewed_facts if item.get("gold_label") != "DISPUTED"
    ]
    correct_facts = sum(
        item.get("gold_label") == "CORRECT" for item in decidable_facts
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": workbench["run_id"],
        "scored_at": datetime.now(UTC).isoformat(),
        "annotation_validation": validation,
        "person_identity": {
            "reviewed_pairs": len(reviewed_people),
            "gold_same_pairs": gold_same_count,
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "true_negative": tn,
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
            "candidate_recall": _metric(candidate_hits, gold_same_count),
        },
        "events": {
            "confirmed_gold_events": len(confirmed_events),
            "discovered_gold_events": event_hits,
            "discovery_recall": _metric(event_hits, len(confirmed_events)),
        },
        "facts": {
            "reviewed_facts": len(reviewed_facts),
            "decidable_facts": len(decidable_facts),
            "correct_facts": correct_facts,
            "correctness": _metric(correct_facts, len(decidable_facts)),
        },
    }


def _format_metric(value: float | None) -> str:
    return "无读数" if value is None else f"{value:.3f}"


def render_report_markdown(report: dict[str, Any]) -> str:
    people = report["person_identity"]
    events = report["events"]
    facts = report["facts"]
    validation = report["annotation_validation"]
    return "\n".join([
        "# 最小金标对拍报告",
        "",
        f"- 运行：`{report['run_id']}`",
        f"- 生成时间：{report['scored_at']}",
        (
            f"- 高风险二次复核：{validation['high_risk_second_reviewed']}/"
            f"{validation['high_risk_reviewed']}"
        ),
        "",
        "## 人物身份归一",
        "",
        f"- 已确认人物对：{people['reviewed_pairs']}",
        f"- 精确率：{_format_metric(people['precision'])}（基线目标 ≥ 0.95）",
        f"- 召回率：{_format_metric(people['recall'])}（基线目标 ≥ 0.85）",
        f"- F1：{_format_metric(people['f1'])}",
        f"- 候选覆盖召回率：{_format_metric(people['candidate_recall'])}",
        "",
        "## 事件发现",
        "",
        f"- 已确认金标事件：{events['confirmed_gold_events']}",
        f"- 已发现：{events['discovered_gold_events']}",
        f"- 发现召回率：{_format_metric(events['discovery_recall'])}（基线目标 ≥ 0.80）",
        "",
        "## 事实正确性",
        "",
        f"- 已复核事实：{facts['reviewed_facts']}",
        f"- 可判定事实：{facts['decidable_facts']}",
        f"- 正确率：{_format_metric(facts['correctness'])}（基线目标 ≥ 0.90）",
        "",
        "> 这些读数是首轮实验结果，不是宣传承诺；无读数表示没有足够人工确认样本。",
        "",
    ])


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def export_command(args: argparse.Namespace) -> None:
    workbench = fetch_workbench(args.api_base, args.run_id)
    evidence_index = fetch_evidence_index(
        args.api_base,
        required_evidence_ids(workbench),
    )
    gold = build_gold_template(
        workbench,
        evidence_index,
        sample_start=args.sample_start,
        sample_chars=args.sample_chars,
    )
    output = Path(args.output).resolve()
    _write_json(output, gold)
    coverage = gold["metadata"]["coverage_at_export"]
    print(f"已生成金标草稿：{output}")
    print(
        "样本范围："
        f"{gold['metadata']['sample_start_char']}～{gold['metadata']['sample_end_char']}；"
        f"人物对 {coverage['person_pairs']}，事件 {coverage['events']}，事实 {coverage['facts']}。"
    )
    missing = []
    if not coverage["person_pairs"]:
        missing.append("别名人物对")
    if not coverage["hard_negative_pairs"]:
        missing.append("共同事件硬负例")
    missing.append("同名人物（需人工标记）")
    if not coverage["cross_chapter_events"]:
        missing.append("跨章事件")
    if coverage["facts"] < 20:
        missing.append(f"核心事实至少 20 条（当前 {coverage['facts']}）")
    if not coverage["temporal_facts"]:
        missing.append("事实失效")
    if missing:
        print("仍需人工补标：" + "、".join(missing) + "。")
    print("草稿不能产生正式读数；请按文件内 instructions 完成人工确认。")


def score_command(args: argparse.Namespace) -> None:
    gold_path = Path(args.gold).resolve()
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    workbench = fetch_workbench(args.api_base, gold["metadata"]["run_id"])
    report = score_gold(gold, workbench, allow_draft=args.allow_draft)
    output_dir = Path(args.output_dir).resolve()
    _write_json(output_dir / "report.json", report)
    markdown = render_report_markdown(report)
    (output_dir / "report.md").write_text(markdown, encoding="utf-8")
    print(f"已生成对拍报告：{output_dir / 'report.md'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="导出 AI 小说拆解最小金标草稿，并对人工确认结果计算质量读数。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export", help="导出约 5 万字金标草稿")
    export_parser.add_argument("--run-id", required=True)
    export_parser.add_argument("--api-base", default="http://127.0.0.1:18000")
    export_parser.add_argument("--output", required=True)
    export_parser.add_argument("--sample-start", type=int)
    export_parser.add_argument("--sample-chars", type=int, default=DEFAULT_SAMPLE_CHARS)
    export_parser.set_defaults(handler=export_command)

    score_parser = subparsers.add_parser("score", help="计算人工金标对拍读数")
    score_parser.add_argument("--gold", required=True)
    score_parser.add_argument("--api-base", default="http://127.0.0.1:18000")
    score_parser.add_argument("--output-dir", required=True)
    score_parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="仅用于验证工具；正式读数不得使用。",
    )
    score_parser.set_defaults(handler=score_command)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
    except (EvaluationError, KeyError, json.JSONDecodeError) as error:
        print(f"金标工具失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
