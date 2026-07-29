from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AnalysisRun,
    CandidateStatus,
    DeepAnalysis,
    EntityCandidate,
    EventCandidate,
    EvidenceSpan,
    NarrativeSynthesis,
    PersonIdentityDecision,
    SourceUnit,
    Task,
    TaskStatus,
)
from .analysis import REQUIRED_CHARACTER_ROSTER_SIZE, narrative_phase_id
from .character_design import build_character_design_projection
from .chapter_end_hooks import build_chapter_end_hooks_projection
from .opening_hook_payoffs import build_opening_hook_payoffs_projection
from .opening_payoff_candidates import (
    build_opening_payoff_candidates_projection,
)
from .opening_structure import build_opening_structure_projection
from .learning_report import assess_learning_report_readiness, build_learning_report_projection


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    return re.sub(r"[【】\[\]（）()\s，。、“”‘’：:!?！？]", "", value).casefold()


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _unique_events(values: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for value in values:
        event_id = str(value.get("id") or "")
        if event_id and event_id not in seen:
            seen.add(event_id)
            result.append(value)
    return result


_INTERNAL_REFERENCE_TOKEN = r"(?:evd|cev|ent|rel|phs|nsy|dpa|tsk|att|art)_[A-Za-z0-9]{8,64}"
_INTERNAL_REFERENCE_LIST = re.compile(
    rf"\s*[\[（(]\s*{_INTERNAL_REFERENCE_TOKEN}(?:\s*[,，]\s*{_INTERNAL_REFERENCE_TOKEN})*\s*[\]）)]"
)
_INTERNAL_REFERENCE = re.compile(rf"(?<![A-Za-z0-9]){_INTERNAL_REFERENCE_TOKEN}(?![A-Za-z0-9])")
_STRUCTURED_REFERENCE_KEYS = {
    "evidence_ids", "event_ids", "trigger_event_id", "source_event_id", "target_event_id",
}


def _clean_legacy_narrative_references(value: object, key: str | None = None) -> object:
    """Hide internal IDs leaked by older model outputs without altering real references."""
    if key in _STRUCTURED_REFERENCE_KEYS:
        return value
    if isinstance(value, dict):
        return {
            item_key: _clean_legacy_narrative_references(item, item_key)
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_clean_legacy_narrative_references(item) for item in value]
    if isinstance(value, str):
        cleaned = _INTERNAL_REFERENCE_LIST.sub("", value)
        cleaned = _INTERNAL_REFERENCE.sub("", cleaned)
        cleaned = re.sub(r"[ \t]+([，。；：！？])", r"\1", cleaned)
        return cleaned.strip()
    return value


_GENERIC_PERSON_ALIASES = {
    "他", "她", "它", "此人", "那人", "男人", "女人", "少年", "少女",
    "老人", "老师", "先生", "女士", "同学", "老板", "经理", "主任",
    "局长", "队长", "医生", "警察", "服务员", "司机",
}
_TITLE_SUFFIXES = (
    "老师", "先生", "女士", "同学", "老板", "经理", "主任", "局长",
    "队长", "医生", "警官", "师傅", "大人", "前辈",
)
_GENERIC_IDENTITY_CANDIDATE_NAMES = {
    *_GENERIC_PERSON_ALIASES,
    "男孩", "女孩", "年轻人", "中年人", "婴儿", "妈妈", "爸爸",
    "父亲", "母亲", "叔叔", "校长", "副校长", "教授", "二副", "大副",
    "轮机长", "龙", "龙王", "父母", "家长",
}


def _specific_person_name(value: str) -> int:
    normalized = _normalize(value)
    if not normalized or normalized in _GENERIC_PERSON_ALIASES:
        return 0
    if any(normalized.endswith(suffix) for suffix in _TITLE_SUFFIXES):
        return 1
    if 2 <= len(normalized) <= 6:
        return 3
    return 2


def _generic_identity_candidate_name(value: str) -> bool:
    normalized = _normalize(value)
    if normalized in _GENERIC_IDENTITY_CANDIDATE_NAMES:
        return True
    return (
        len(normalized) > 6
        and any(
            normalized.endswith(suffix)
            for suffix in (
                "男人", "女人", "男孩", "女孩", "年轻人", "中年人",
                "老人", "少年", "少女", "婴儿", "妈妈", "爸爸",
                "父亲", "母亲", "叔叔",
            )
        )
    )


def _person_groups(entities: list[EntityCandidate]) -> list[list[EntityCandidate]]:
    people = [item for item in entities if item.entity_type == "PERSON"]
    aliases = [
        {
            normalized
            for alias in _read_json(item.aliases_json)
            if (normalized := _normalize(alias))
            and normalized not in _GENERIC_PERSON_ALIASES
        }
        for item in people
    ]
    parents = list(range(len(people)))
    sizes = [1] * len(people)

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for left in range(len(people)):
        left_name = _normalize(people[left].name)
        for right in range(left + 1, len(people)):
            right_name = _normalize(people[right].name)
            if not left_name or not right_name:
                continue
            left_names_right = right_name in aliases[left]
            right_names_left = left_name in aliases[right]
            directly_linked = (
                left_names_right
                and right_names_left
            ) or (
                (left_names_right or right_names_left)
                and min(
                    _specific_person_name(people[left].name),
                    _specific_person_name(people[right].name),
                ) <= 1
            )
            if not directly_linked:
                continue
            left_root = find(left)
            right_root = find(right)
            # Do not create an unreviewed transitive alias chain. A third name
            # remains separate until a later evidence-backed resolution step.
            if left_root == right_root or sizes[left_root] > 1 or sizes[right_root] > 1:
                continue
            parents[right_root] = left_root
            sizes[left_root] += sizes[right_root]

    grouped: dict[int, list[EntityCandidate]] = {}
    for index, person in enumerate(people):
        grouped.setdefault(find(index), []).append(person)
    return list(grouped.values())


def _safe_person_group_names(
    group: list[EntityCandidate],
    all_canonical_names: set[str],
) -> list[str]:
    """Keep aliases unless they collide with another unmerged named person."""
    group_canonical_names = {
        _normalize(item.name)
        for item in group
        if _normalize(item.name)
    }
    return _unique([
        name
        for item in group
        for name in [item.name, *_read_json(item.aliases_json)]
        if (
            (normalized := _normalize(name))
            and (
                normalized in group_canonical_names
                or normalized not in all_canonical_names
            )
        )
    ])


def _required_role_character_ids(characters: list[dict]) -> set[str]:
    """Mirror the model roster ranking so readiness and validation agree."""
    ranked = sorted(
        characters,
        key=lambda item: (
            -int(item.get("appearance_count") or 0),
            -int(item.get("confidence") or 0),
            str(item.get("name") or ""),
        ),
    )
    return {
        str(item.get("id") or "")
        for item in ranked[:REQUIRED_CHARACTER_ROSTER_SIZE]
        if item.get("id")
    }


def _canonical_person(group: list[EntityCandidate]) -> EntityCandidate:
    return max(
        group,
        key=lambda item: (
            _specific_person_name(item.name),
            item.confidence,
            len(item.description),
            -len(item.name),
        ),
    )


def _apply_person_resolutions(
    run_id: str,
    characters: list[dict],
    events: list[dict],
    resolutions: list[dict],
) -> list[dict]:
    """Apply evidence-backed person resolutions to the read projection only.

    The source candidates stay immutable. A later analysis revision can remove
    or replace a resolution, which makes the merge reversible without data
    surgery.
    """
    canonical_by_name: dict[str, str] = {}
    resolution_by_canonical: dict[str, dict] = {}
    for resolution in resolutions:
        if resolution.get("entity_type") != "PERSON":
            continue
        canonical = str(resolution.get("canonical_name") or "").strip()
        if not canonical:
            continue
        normalized_canonical = _normalize(canonical)
        resolution_by_canonical[normalized_canonical] = resolution
        for name in resolution.get("merged_names", []):
            normalized = _normalize(str(name))
            if normalized:
                canonical_by_name[normalized] = canonical
    if not canonical_by_name:
        return characters

    for event in events:
        event["people"] = _unique([
            canonical_by_name.get(_normalize(name), name)
            for name in event.get("people", [])
        ])

    groups: dict[str, list[dict]] = {}
    canonical_label: dict[str, str] = {}
    for character in characters:
        names = [character.get("name", ""), *character.get("aliases", [])]
        canonical = next(
            (
                canonical_by_name[_normalize(name)]
                for name in names
                if _normalize(name) in canonical_by_name
            ),
            str(character.get("name") or ""),
        )
        normalized = _normalize(canonical)
        canonical_label[normalized] = canonical
        groups.setdefault(normalized, []).append(character)

    merged: list[dict] = []
    for normalized, group in groups.items():
        canonical = canonical_label[normalized]
        resolution = resolution_by_canonical.get(normalized)
        evidence_ids = _unique([
            evidence_id
            for character in group
            for evidence_id in character.get("evidence_ids", [])
        ] + (resolution.get("evidence_ids", []) if resolution else []))
        event_ids = _unique([
            event_id
            for character in group
            for event_id in character.get("event_ids", [])
        ])
        aliases = _unique([
            name
            for character in group
            for name in [character.get("name", ""), *character.get("aliases", [])]
            if _normalize(name) != normalized
        ])
        identity_notes = _unique([
            note
            for character in group
            for note in character.get("identity_notes", [])
        ])
        if resolution:
            reason = str(resolution.get("reason") or "原文直接身份关系").strip()
            resolution_label = {
                "ALIAS": "别名",
                "TITLE": "称谓",
                "PSEUDONYM": "化名",
                "FALSE_IDENTITY": "假身份",
                "IDENTITY_REVEAL": "身份揭示",
            }.get(str(resolution.get("resolution_type") or "ALIAS"), "身份关系")
            valid_from = resolution.get("valid_from_chapter")
            valid_to = resolution.get("valid_to_chapter")
            range_note = (
                f"（第 {valid_from} 至 {valid_to} 章）"
                if valid_from and valid_to
                else f"（第 {valid_from} 章起）" if valid_from
                else f"（截至第 {valid_to} 章）" if valid_to
                else ""
            )
            identity_notes.append(
                f"依据原文{resolution_label}{range_note}合并展示：{'、'.join(resolution.get('merged_names', []))}。依据：{reason}"
            )
        first_candidates = [
            character.get("first_chapter_ordinal")
            for character in group
            if character.get("first_chapter_ordinal") is not None
        ]
        last_candidates = [
            character.get("last_chapter_ordinal")
            for character in group
            if character.get("last_chapter_ordinal") is not None
        ]
        first_ordinal = min(first_candidates) if first_candidates else None
        last_ordinal = max(last_candidates) if last_candidates else None
        first_title = next(
            (
                character.get("first_chapter_title")
                for character in group
                if character.get("first_chapter_ordinal") == first_ordinal
            ),
            None,
        )
        last_title = next(
            (
                character.get("last_chapter_title")
                for character in group
                if character.get("last_chapter_ordinal") == last_ordinal
            ),
            None,
        )
        activity_level = (
            "高" if len(event_ids) >= 3 or len(evidence_ids) >= 4
            else "中" if event_ids or len(evidence_ids) >= 2
            else "低"
        )
        merged.append({
            "id": f"chr_{_hash(f'{run_id}:{normalized}')[:32]}",
            "name": canonical,
            "aliases": aliases,
            "description": max((str(item.get("description") or "") for item in group), key=len),
            "evidence_ids": evidence_ids,
            "event_ids": event_ids,
            "first_chapter_ordinal": first_ordinal,
            "first_chapter_title": first_title,
            "last_chapter_ordinal": last_ordinal,
            "last_chapter_title": last_title,
            "appearance_count": len(evidence_ids),
            "activity_level": activity_level,
            "status": "UNCERTAIN" if resolution or any(item.get("status") == "UNCERTAIN" for item in group) else "VALID",
            "confidence": max(int(item.get("confidence") or 0) for item in group),
            "identity_notes": identity_notes,
        })
    merged.sort(key=lambda item: item["name"])
    return merged


def _canonical_person_name_map(characters: list[dict]) -> dict[str, str]:
    """Map only names and aliases that belong to exactly one projected person."""
    owners: dict[str, set[str]] = {}
    for character in characters:
        canonical = str(character.get("name") or "").strip()
        if not canonical:
            continue
        for name in [canonical, *character.get("aliases", [])]:
            normalized = _normalize(str(name))
            if normalized:
                owners.setdefault(normalized, set()).add(canonical)
    return {
        normalized: next(iter(canonical_names))
        for normalized, canonical_names in owners.items()
        if len(canonical_names) == 1
    }


def _person_identity_candidate_suggestions(
    entities: list[EntityCandidate],
    characters: list[dict],
    events: list[dict],
    decisions: list[PersonIdentityDecision],
) -> list[dict]:
    """Find unresolved direct identity links without merging them automatically."""
    canonical_characters = {
        _normalize(str(character.get("name") or "")): character
        for character in characters
        if _normalize(str(character.get("name") or ""))
    }
    owners: dict[str, set[str]] = {}
    for character in characters:
        canonical = str(character.get("name") or "")
        for name in [canonical, *character.get("aliases", [])]:
            normalized = _normalize(str(name))
            if normalized:
                owners.setdefault(normalized, set()).add(canonical)

    def owner(name: str) -> str | None:
        normalized = _normalize(name)
        exact = canonical_characters.get(normalized)
        if exact is not None:
            return str(exact["name"])
        claimed = owners.get(normalized, set())
        return next(iter(claimed)) if len(claimed) == 1 else None

    people = [entity for entity in entities if entity.entity_type == "PERSON"]
    raw_names: dict[str, list[EntityCandidate]] = {}
    for entity in people:
        normalized = _normalize(entity.name)
        if normalized:
            raw_names.setdefault(normalized, []).append(entity)

    links: dict[str, dict] = {}
    for entity in people:
        source_owner = owner(entity.name)
        if not source_owner:
            continue
        for alias in _read_json(entity.aliases_json):
            normalized_alias = _normalize(alias)
            for target in raw_names.get(normalized_alias, []):
                target_owner = owner(target.name)
                if not target_owner or target_owner == source_owner:
                    continue
                ordered = sorted(
                    (source_owner, target_owner),
                    key=lambda value: (_normalize(value), value),
                )
                pair_key = f"pic_{_hash(':'.join(_normalize(value) for value in ordered))[:32]}"
                link = links.setdefault(pair_key, {
                    "candidate_key": pair_key,
                    "left_name": ordered[0],
                    "right_name": ordered[1],
                    "directions": set(),
                })
                link["directions"].add((source_owner, target_owner))

    decided_keys = {decision.pair_key for decision in decisions}
    character_by_name = {
        str(character["name"]): character
        for character in characters
    }
    event_owner_sets: list[set[str]] = []
    for event in events:
        event_owner_sets.append({
            participant_owner
            for name in event.get("people", [])
            if (participant_owner := owner(str(name)))
        })

    suggestions: list[dict] = []
    for pair_key, link in links.items():
        if pair_key in decided_keys:
            continue
        left = character_by_name.get(link["left_name"])
        right = character_by_name.get(link["right_name"])
        if left is None or right is None:
            continue
        directions: set[tuple[str, str]] = link["directions"]
        reciprocal = (
            (left["name"], right["name"]) in directions
            and (right["name"], left["name"]) in directions
        )
        left_evidence = list(left.get("evidence_ids", []))
        right_evidence = list(right.get("evidence_ids", []))
        shared_evidence = [
            evidence_id
            for evidence_id in left_evidence
            if evidence_id in set(right_evidence)
        ]
        evidence_ids = _unique([
            *shared_evidence[:2],
            *left_evidence[:2],
            *right_evidence[:2],
        ])[:4]
        cooccurrence_count = sum(
            left["name"] in owners_in_event and right["name"] in owners_in_event
            for owners_in_event in event_owner_sets
        )
        recommended = max(
            (left, right),
            key=lambda item: (
                _specific_person_name(str(item["name"])),
                int(item.get("appearance_count") or 0),
                int(item.get("confidence") or 0),
                -len(str(item["name"])),
            ),
        )["name"]
        signals = ["候选人物的别名直接指向另一人物"]
        if reciprocal:
            signals.append("双方互相把对方列为别名")
        if shared_evidence:
            signals.append(f"共享 {len(shared_evidence)} 处原文证据")
        if cooccurrence_count:
            signals.append(f"有 {cooccurrence_count} 个事件同时提到双方，需重点核对")
        confidence = 95 if reciprocal else 82
        if shared_evidence:
            confidence = max(confidence, 88)
        if cooccurrence_count:
            confidence = max(40, confidence - 25)
        review_priority = (
            "BLOCKING"
            if (
                not _generic_identity_candidate_name(str(left["name"]))
                and not _generic_identity_candidate_name(str(right["name"]))
                and cooccurrence_count == 0
            )
            else "OPTIONAL"
        )
        direction_labels = [
            f"“{source}”的别名中出现“{target}”"
            for source, target in sorted(directions)
        ]
        suggestions.append({
            "candidate_key": pair_key,
            "left_name": left["name"],
            "right_name": right["name"],
            "left_aliases": list(left.get("aliases", [])),
            "right_aliases": list(right.get("aliases", [])),
            "recommended_name": recommended,
            "reason": "；".join(direction_labels),
            "signals": signals,
            "confidence": confidence,
            "review_priority": review_priority,
            "recommended_decision": (
                "DIFFERENT" if cooccurrence_count else "SAME"
            ),
            "cooccurrence_count": cooccurrence_count,
            "evidence_ids": evidence_ids,
        })
    suggestions.sort(
        key=lambda item: (
            item["review_priority"] != "BLOCKING",
            -int(item["confidence"]),
            item["left_name"],
            item["right_name"],
        )
    )
    return suggestions


def _project_canonical_person_names(
    characters: list[dict],
    events: list[dict],
    phases: list[dict],
    story_overview: dict | None,
    character_relations: list[dict],
    deep_payload: dict | None,
) -> None:
    """Propagate the final person projection through every structured consumer."""
    canonical_by_name = _canonical_person_name_map(characters)
    if not canonical_by_name:
        return

    def canonical(name: object) -> object:
        if not isinstance(name, str):
            return name
        return canonical_by_name.get(_normalize(name), name)

    for event in events:
        event["people"] = _unique([
            str(canonical(name))
            for name in event.get("people", [])
        ])
    for phase in phases:
        phase["people"] = _unique([
            str(canonical(name))
            for name in phase.get("people", [])
        ])
    if story_overview is not None:
        story_overview["protagonist"] = canonical(
            story_overview.get("protagonist", "")
        )
    for relation in character_relations:
        relation["source_name"] = canonical(relation.get("source_name", ""))
        relation["target_name"] = canonical(relation.get("target_name", ""))
    if deep_payload is None:
        return
    for fact in deep_payload.get("fact_versions", []):
        fact["subject"] = canonical(fact.get("subject", ""))
    for change in deep_payload.get("state_changes", []):
        change["subject"] = canonical(change.get("subject", ""))
    for knowledge in deep_payload.get("actor_knowledge", []):
        knowledge["actor"] = canonical(knowledge.get("actor", ""))
    for transfer in deep_payload.get("knowledge_transfers", []):
        transfer["source_actor"] = canonical(transfer.get("source_actor", ""))
        transfer["target_actor"] = canonical(transfer.get("target_actor", ""))
    for conflict in deep_payload.get("conflicts", []):
        conflict["participants"] = _unique([
            str(canonical(name))
            for name in conflict.get("participants", [])
        ])


def _event_candidate_groups(
    candidates: list[EventCandidate],
) -> list[tuple[tuple[str, str, str], list[EventCandidate]]]:
    """Union duplicate event proposals only when they share exact evidence.

    This covers overlapping extraction batches and alternate model titles while
    keeping similar events in different passages separate.
    """
    groups: list[tuple[tuple[str, str, str], list[EventCandidate], set[str]]] = []
    for event in sorted(candidates, key=lambda item: (item.start_char, item.id)):
        details = _read_object(event.details_json)
        narrative_mode = str(details.get("narrative_mode") or "UNCERTAIN")
        exact_key = (_normalize(event.title), event.event_type, narrative_mode)
        evidence_ids = set(_read_json(event.evidence_ids_json))
        matched_index: int | None = None
        for index, (key, items, group_evidence) in enumerate(groups):
            same_contract = key[1:] == exact_key[1:]
            overlaps_existing_span = any(
                event.start_char < existing.end_char
                and existing.start_char < event.end_char
                for existing in items
            )
            if same_contract and (
                evidence_ids.intersection(group_evidence)
                or (key == exact_key and overlaps_existing_span)
            ):
                matched_index = index
                break
        if matched_index is None:
            groups.append((exact_key, [event], set(evidence_ids)))
            continue
        key, items, group_evidence = groups[matched_index]
        items.append(event)
        group_evidence.update(evidence_ids)
        # Prefer the most frequent exact title later, but keep the first stable
        # key so grouping is deterministic across retries.
        groups[matched_index] = (key, items, group_evidence)
    return [(key, items) for key, items, _evidence in groups]


@dataclass(frozen=True, slots=True)
class _ChapterRef:
    ordinal: int
    title: str
    start_char: int
    end_char: int


def _chapters(session: Session, source_version_id: str) -> list[_ChapterRef]:
    units = list(session.scalars(
        select(SourceUnit)
        .where(
            SourceUnit.source_version_id == source_version_id,
            SourceUnit.unit_type == "CHAPTER",
        )
        .order_by(SourceUnit.ordinal)
    ))
    # The book title/front matter is a SourceUnit but is not a novel chapter.
    # Use the chapter sequence for user-facing numbering and time ordering.
    return [
        _ChapterRef(index, item.title, item.start_char, item.end_char)
        for index, item in enumerate(units, start=1)
    ]


def _chapters_for_range(chapters: list[_ChapterRef], start_char: int, end_char: int) -> list[_ChapterRef]:
    return [
        chapter
        for chapter in chapters
        if chapter.start_char < end_char and chapter.end_char > start_char
    ]


def _read_json(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def _read_object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ranges_overlap(left: dict, right: dict) -> bool:
    left_end = left.get("valid_to_chapter") or 10**12
    right_end = right.get("valid_to_chapter") or 10**12
    return (
        int(left.get("valid_from_chapter") or 1) <= int(right_end)
        and int(right.get("valid_from_chapter") or 1) <= int(left_end)
    )


def _annotate_fact_timeline(facts: list[dict]) -> None:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for fact in facts:
        grouped.setdefault(
            (_normalize(str(fact.get("subject") or "")), _normalize(str(fact.get("predicate") or ""))),
            [],
        ).append(fact)

    for group in grouped.values():
        ordered = sorted(
            group,
            key=lambda item: (
                int(item.get("valid_from_chapter") or 1),
                int(item.get("valid_to_chapter") or 10**12),
                str(item.get("id") or ""),
            ),
        )
        seen_values: set[str] = set()
        for index, fact in enumerate(ordered, start=1):
            fact["timeline_version"] = index
            normalized_value = _normalize(str(fact.get("value") or ""))
            conflicts = [
                other
                for other in ordered
                if other is not fact
                and _normalize(str(other.get("value") or "")) != normalized_value
                and _ranges_overlap(fact, other)
            ]
            if conflicts or fact.get("status") == "DISPUTED":
                fact["timeline_status"] = "CONFLICTING"
                fact["timeline_note"] = "同一时间范围存在不同说法或反面证据，系统保留双方，不自动选定一个真值。"
            elif normalized_value in seen_values:
                fact["timeline_status"] = "REESTABLISHED"
                fact["timeline_note"] = "这一事实曾经失效，后来根据新的原文依据再次成立。"
            elif fact.get("valid_to_chapter") is not None:
                fact["timeline_status"] = "EXPIRED"
                fact["timeline_note"] = "这一版本只在标明的章节范围内成立，后续没有覆盖历史记录。"
            else:
                fact["timeline_status"] = "ACTIVE"
                fact["timeline_note"] = "这一版本从标明章节起成立，当前尚未发现明确失效点。"
            seen_values.add(normalized_value)


def _candidate_query(session: Session, model, run_id: str):
    return session.scalars(
        select(model)
        .join(Task, Task.id == model.created_by_task_id)
        .where(
            model.run_id == run_id,
            model.status != CandidateStatus.REJECTED.value,
            Task.status == TaskStatus.SUCCEEDED.value,
            Task.current_attempt_id == model.created_by_attempt_id,
        )
    )


def build_workbench_projection(
    session: Session,
    run_id: str,
    *,
    include_synthesis: bool = True,
    include_question_evidence: bool = True,
    deep_revision: int | None = None,
) -> dict:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")

    entities = list(_candidate_query(session, EntityCandidate, run_id))
    event_candidates = list(_candidate_query(session, EventCandidate, run_id))
    chapters = _chapters(session, run.source_version_id)
    evidence_by_id = {
        evidence.id: evidence
        for evidence in session.scalars(
            select(EvidenceSpan).where(EvidenceSpan.source_version_id == run.source_version_id)
        )
    }

    person_groups = _person_groups(entities)
    canonical_person_names = {
        _normalize(entity.name)
        for entity in entities
        if entity.entity_type == "PERSON" and _normalize(entity.name)
    }
    person_names: set[str] = set()
    for group in person_groups:
        names = [
            name
            for entity in group
            for name in [entity.name, *_read_json(entity.aliases_json)]
        ]
        for name in names:
            normalized = _normalize(name)
            if normalized:
                person_names.add(normalized)

    related_entities = [entity for entity in entities if entity.entity_type != "PERSON"]

    def event_chapters(event: EventCandidate) -> list[_ChapterRef]:
        spans = [evidence_by_id[item] for item in _read_json(event.evidence_ids_json) if item in evidence_by_id]
        if spans:
            start = min(item.start_char for item in spans)
            end = max(item.end_char for item in spans)
        else:
            start, end = event.start_char, event.end_char
        return _chapters_for_range(chapters, start, max(start + 1, end))

    # Exact title/type matches and shared-evidence matches from overlapping
    # batches are safe enough for the read projection. Semantic similarity
    # without shared source evidence remains separate.
    grouped_events = _event_candidate_groups(event_candidates)

    events: list[dict] = []
    event_id_by_candidate: dict[str, str] = {}
    for (normalized_title, event_type, narrative_mode), group in grouped_events:
        group = sorted(group, key=lambda item: (item.start_char, item.id))
        canonical_id = f"cev_{_hash(f'{run_id}:{normalized_title}:{event_type}:{narrative_mode}')[:32]}"
        evidence_ids = _unique([item for event in group for item in _read_json(event.evidence_ids_json)])
        event_details = [_read_object(item.details_json) for item in group]
        discovery_routes = _unique([
            str(route)
            for details in event_details
            for route in details.get("discovery_routes", [])
            if route
        ])

        def longest_detail(name: str) -> str:
            values = [str(item.get(name) or "") for item in event_details]
            return max(values, key=len, default="")

        evidence_spans = [evidence_by_id[item] for item in evidence_ids if item in evidence_by_id]
        if not evidence_spans:
            boundary_status = "UNRESOLVED"
            boundary_note = "事件边界缺少可用的原文依据。"
        elif len(evidence_spans) == 1:
            boundary_status = "EXACT_SPAN"
            boundary_note = "事件边界由一段连续原文依据定位。"
        else:
            ordered_spans = sorted(evidence_spans, key=lambda item: item.start_char)
            largest_gap = max(
                (
                    current.start_char - previous.end_char
                    for previous, current in zip(ordered_spans, ordered_spans[1:])
                ),
                default=0,
            )
            boundary_status = "MULTI_SPAN" if largest_gap > 1 else "EXACT_SPAN"
            boundary_note = (
                "该事件由多段分离的原文依据共同描述，页面没有把中间文字误算成事件正文。"
                if boundary_status == "MULTI_SPAN"
                else "事件边界由相邻的多段原文依据共同定位。"
            )
        people: list[str] = []
        related: list[str] = []
        chapter_refs: list[_ChapterRef] = []
        for event in group:
            for participant in _read_json(event.participants_json):
                if _normalize(participant) in person_names:
                    people.append(participant)
                else:
                    related.append(participant)
            chapter_refs.extend(event_chapters(event))
            event_id_by_candidate[event.id] = canonical_id
        chapter_refs = sorted({item.ordinal: item for item in chapter_refs}.values(), key=lambda item: item.ordinal)
        events.append({
            "id": canonical_id,
            "title": group[0].title,
            "event_type": event_type,
            "summary": max((item.summary for item in group), key=len),
            "people": _unique(people),
            "related_entities": _unique(related),
            "evidence_ids": evidence_ids,
            "chapter_ordinals": [item.ordinal for item in chapter_refs],
            "chapter_titles": [item.title for item in chapter_refs],
            "start_char": min(item.start_char for item in group),
            "end_char": max(item.end_char for item in group),
            "mention_count": len(group),
            "status": "UNCERTAIN" if any(item.status == CandidateStatus.UNCERTAIN.value for item in group) else "VALID",
            "confidence": max(item.confidence for item in group),
            "narrative_mode": narrative_mode,
            "location": longest_detail("location"),
            "trigger": longest_detail("trigger") or "当前原文证据没有明确说明直接起因。",
            "process": longest_detail("process") or max((item.summary for item in group), key=len),
            "outcome": longest_detail("outcome") or "当前原文证据没有明确说明事件结果。",
            "impact": longest_detail("impact") or "当前原文证据没有明确说明后续影响。",
            "boundary_status": boundary_status,
            "boundary_note": boundary_note,
            "discovery_routes": discovery_routes,
        })
    events.sort(key=lambda item: (item["start_char"], item["title"]))

    characters: list[dict] = []
    for group in sorted(person_groups, key=lambda items: _canonical_person(items).name):
        entity = _canonical_person(group)
        group_names = _safe_person_group_names(group, canonical_person_names)
        aliases = [name for name in group_names if _normalize(name) != _normalize(entity.name)]
        normalized_group_names = {_normalize(name) for name in group_names}
        evidence_ids = _unique([
            evidence_id
            for item in group
            for evidence_id in _read_json(item.evidence_ids_json)
        ])
        refs = [
            evidence_by_id[item]
            for item in evidence_ids
            if item in evidence_by_id
        ]
        character_event_ids = [
            event["id"]
            for event in events
            if any(_normalize(person) in normalized_group_names for person in event["people"])
        ]
        chapter_refs: list[_ChapterRef] = []
        for evidence in refs:
            chapter_refs.extend(_chapters_for_range(chapters, evidence.start_char, evidence.end_char))
        chapter_refs = sorted({item.ordinal: item for item in chapter_refs}.values(), key=lambda item: item.ordinal)
        appearance_count = len(evidence_ids)
        activity_level = "高" if len(character_event_ids) >= 3 or appearance_count >= 4 else "中" if character_event_ids or appearance_count >= 2 else "低"
        characters.append({
            "id": f"chr_{_hash(f'{run_id}:{entity.normalized_name}')[:32]}",
            "name": entity.name,
            "aliases": aliases,
            "description": max((item.description for item in group), key=len),
            "evidence_ids": evidence_ids,
            "event_ids": character_event_ids,
            "first_chapter_ordinal": chapter_refs[0].ordinal if chapter_refs else None,
            "first_chapter_title": chapter_refs[0].title if chapter_refs else None,
            "last_chapter_ordinal": chapter_refs[-1].ordinal if chapter_refs else None,
            "last_chapter_title": chapter_refs[-1].title if chapter_refs else None,
            "appearance_count": appearance_count,
            "activity_level": activity_level,
            "status": (
                "UNCERTAIN"
                if len(group) > 1 or any(item.status == CandidateStatus.UNCERTAIN.value for item in group)
                else "VALID"
            ),
            "confidence": max(item.confidence for item in group),
            "identity_notes": (
                [f"依据直接别名关系合并展示：{'、'.join(item.name for item in group)}。建议通过原文依据抽查身份。"]
                if len(group) > 1
                else []
            ),
        })

    synthesis = session.scalar(
        select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run_id)
    ) if include_synthesis else None
    deep_query = select(DeepAnalysis).where(DeepAnalysis.run_id == run_id)
    if deep_revision is None:
        deep_query = deep_query.order_by(DeepAnalysis.revision_no.desc())
    else:
        deep_query = deep_query.where(DeepAnalysis.revision_no == deep_revision)
    deep_analysis = session.scalar(deep_query) if include_synthesis else None
    if include_synthesis and deep_revision is not None and deep_analysis is None:
        raise ValueError("DEEP_ANALYSIS_REVISION_NOT_FOUND")
    narrative_status = "NOT_GENERATED"
    story_overview = None
    character_relations: list[dict] = []
    event_relations: list[dict] = []
    phases: list[dict] = []
    role_by_name: dict[str, dict] = {}
    event_reference_map: dict[str, dict] = {}
    for event in events:
        event_reference_map[event["id"]] = event
        legacy_identity = f"{run_id}:{_normalize(event['title'])}:{event['event_type']}"
        legacy_id = f"cev_{_hash(legacy_identity)[:32]}"
        event_reference_map[legacy_id] = event
    if synthesis is not None:
        narrative_status = "READY"
        payload = _clean_legacy_narrative_references(
            json.loads(synthesis.payload_json)
        )
        story_overview = payload.get("story_overview")
        role_by_name = {
            _normalize(item.get("name", "")): item
            for item in payload.get("character_roles", [])
            if item.get("name")
        }
        character_relations = payload.get("character_relations", [])
        event_relations = payload.get("event_relations", [])
        event_by_id = {item["id"]: item for item in events}
        for index, phase in enumerate(payload.get("narrative_phases", []), start=1):
            phase_events = _unique_events([
                event_reference_map[event_id]
                for event_id in phase.get("event_ids", [])
                if event_id in event_reference_map
            ])
            phase_evidence_ids = phase.get("evidence_ids", [])
            referenced_evidence_ids = set(phase_evidence_ids)
            phase_events = _unique_events([
                *phase_events,
                *[
                    event
                    for event in events
                    if referenced_evidence_ids.intersection(event.get("evidence_ids", []))
                ],
            ])
            phase_event_ids = [event["id"] for event in phase_events]
            chapter_ordinals = sorted({
                chapter
                for event in phase_events
                for chapter in event["chapter_ordinals"]
            })
            chapter_titles = []
            for event in phase_events:
                for title in event["chapter_titles"]:
                    if title not in chapter_titles:
                        chapter_titles.append(title)
            if not chapter_ordinals:
                phase_chapters: list[_ChapterRef] = []
                for evidence_id in phase_evidence_ids:
                    evidence = evidence_by_id.get(evidence_id)
                    if evidence is not None:
                        phase_chapters.extend(
                            _chapters_for_range(chapters, evidence.start_char, evidence.end_char)
                        )
                phase_chapters = sorted(
                    {item.ordinal: item for item in phase_chapters}.values(),
                    key=lambda item: item.ordinal,
                )
                chapter_ordinals = [item.ordinal for item in phase_chapters]
                chapter_titles = [item.title for item in phase_chapters]
            phase_projection = {
                "title": phase["title"],
                "summary": phase["situation"],
                "situation": phase["situation"],
                "goal": phase.get("goal", ""),
                "obstacle": phase.get("obstacle", ""),
                "key_actions": phase.get("key_actions", []),
                "outcome": phase.get("outcome", ""),
                "change": phase.get("change", ""),
                "next_hook": phase.get("next_hook", ""),
                "event_ids": phase_event_ids,
                "evidence_ids": phase_evidence_ids,
                "chapter_ordinals": chapter_ordinals,
                "chapter_titles": chapter_titles,
                "people": _unique([person for event in phase_events for person in event["people"]]),
            }
            phases.append({
                "id": narrative_phase_id(run_id, phase_projection),
                **phase_projection,
            })
        valid_event_relations: list[dict] = []
        for relation in event_relations:
            source = event_reference_map.get(relation.get("source_event_id"))
            target = event_reference_map.get(relation.get("target_event_id"))
            if source is not None and target is not None:
                relation["source_event_id"] = source["id"]
                relation["target_event_id"] = target["id"]
                relation["source_title"] = source["title"]
                relation["target_title"] = target["title"]
                valid_event_relations.append(relation)
        event_relations = valid_event_relations

    deep_status = "NOT_GENERATED"
    deep_payload = None
    deep_revision = None
    if deep_analysis is not None:
        deep_status = (
            "OUTDATED"
            if synthesis is not None
            and deep_revision is None
            and synthesis.created_at > deep_analysis.created_at
            else "READY"
        )
        deep_payload = json.loads(deep_analysis.payload_json)
        deep_revision = deep_analysis.revision_no
        _annotate_fact_timeline(deep_payload.get("fact_versions", []))
        for rule in deep_payload.get("world_rules", []):
            rule_chapters: list[_ChapterRef] = []
            for evidence_id in rule.get("evidence_ids", []):
                evidence = evidence_by_id.get(evidence_id)
                if evidence is not None:
                    rule_chapters.extend(
                        _chapters_for_range(chapters, evidence.start_char, evidence.end_char)
                    )
            rule["discovered_chapter"] = min(
                (chapter.ordinal for chapter in rule_chapters),
                default=1,
            )

    resolution_by_name: dict[str, str] = {}
    entity_resolutions: list[dict] = (
        list(deep_payload.get("entity_resolutions", []))
        if deep_payload is not None
        else []
    )
    person_identity_decisions = list(session.scalars(
        select(PersonIdentityDecision)
        .where(PersonIdentityDecision.run_id == run_id)
        .order_by(PersonIdentityDecision.created_at, PersonIdentityDecision.id)
    ))
    different_pairs = [{
        _normalize(decision.left_name),
        _normalize(decision.right_name),
    } for decision in person_identity_decisions if decision.decision == "DIFFERENT"]
    entity_resolutions = [
        resolution
        for resolution in entity_resolutions
        if not any(
            pair.issubset({
                _normalize(str(name))
                for name in resolution.get("merged_names", [])
            })
            for pair in different_pairs
        )
    ]
    entity_resolutions.extend([
        {
            "entity_type": "PERSON",
            "canonical_name": decision.canonical_name or decision.left_name,
            "merged_names": [decision.left_name, decision.right_name],
            "resolution_type": "ALIAS",
            "reason": "用户在人物身份候选中确认这两个名字属于同一人物。",
            "evidence_ids": [],
        }
        for decision in person_identity_decisions
        if decision.decision == "SAME"
    ])
    for resolution in entity_resolutions:
        if resolution.get("entity_type") == "PERSON":
            continue
        canonical = resolution.get("canonical_name", "")
        for name in resolution.get("merged_names", []):
            resolution_by_name[_normalize(name)] = canonical
    if resolution_by_name:
        for event in events:
            event["related_entities"] = _unique([
                resolution_by_name.get(_normalize(name), name)
                for name in event["related_entities"]
            ])
    characters = _apply_person_resolutions(
        run_id,
        characters,
        events,
        entity_resolutions,
    )
    person_identity_candidates = _person_identity_candidate_suggestions(
        entities,
        characters,
        events,
        person_identity_decisions,
    )
    projected_person_names = _canonical_person_name_map(characters)
    person_identity_candidates = [
        candidate
        for candidate in person_identity_candidates
        if projected_person_names.get(_normalize(candidate["left_name"]), candidate["left_name"])
        != projected_person_names.get(_normalize(candidate["right_name"]), candidate["right_name"])
    ]
    _project_canonical_person_names(
        characters,
        events,
        phases,
        story_overview,
        character_relations,
        deep_payload,
    )

    grouped_related: dict[tuple[str, str], list[EntityCandidate]] = {}
    for entity in related_entities:
        canonical = resolution_by_name.get(_normalize(entity.name), entity.name)
        grouped_related.setdefault((entity.entity_type, _normalize(canonical)), []).append(entity)
    related_projection: list[dict] = []
    for (entity_type, normalized_canonical), group in grouped_related.items():
        canonical = resolution_by_name.get(_normalize(group[0].name), group[0].name)
        aliases = _unique([
            alias
            for entity in group
            for alias in [
                *(name for name in [entity.name] if name != canonical),
                *_read_json(entity.aliases_json),
            ]
            if alias != canonical
        ])
        related_projection.append({
            "id": f"ent_{_hash(f'{run_id}:{entity_type}:{normalized_canonical}')[:32]}",
            "run_id": run_id,
            "source_version_id": run.source_version_id,
            "name": canonical,
            "entity_type": entity_type,
            "aliases": aliases,
            "description": max((entity.description for entity in group), key=len),
            "evidence_ids": _unique([
                evidence_id
                for entity in group
                for evidence_id in _read_json(entity.evidence_ids_json)
            ]),
            "status": (
                "UNCERTAIN"
                if any(entity.status == CandidateStatus.UNCERTAIN.value for entity in group)
                else "VALID"
            ),
            "confidence": max(entity.confidence for entity in group),
        })
    related_projection.sort(key=lambda item: (item["entity_type"], item["name"]))

    required_role_character_ids = _required_role_character_ids(characters)
    for character in characters:
        role = next(
            (
                role_by_name[name]
                for name in [_normalize(character["name"]), *(_normalize(alias) for alias in character["aliases"])]
                if name in role_by_name
            ),
            None,
        )
        character.update({
            "role_required": character["id"] in required_role_character_ids,
            "role": role.get("role", "UNCLASSIFIED") if role else "UNCLASSIFIED",
            "role_reason": role.get("role_reason", "尚未完成角色定位") if role else "尚未完成角色定位",
            "identities": role.get("identities", []) if role else [],
            "goals": role.get("goals", []) if role else [],
            "motivations": role.get("motivations", []) if role else [],
            "abilities": role.get("abilities", []) if role else [],
            "secrets": role.get("secrets", []) if role else [],
            "important_experiences": role.get("important_experiences", []) if role else [],
            "current_state": role.get("current_state", "") if role else "",
            "arc_summary": role.get("arc_summary", "") if role else "",
        })
    if synthesis is not None and any(
        character["role_required"]
        and character["role"] == "UNCLASSIFIED"
        for character in characters
    ):
        narrative_status = "INCOMPLETE"

    projection = {
        "run_id": run_id,
        "source_version_id": run.source_version_id,
        "status": run.status,
        "characters": characters,
        "person_identity_candidates": person_identity_candidates,
        "person_identity_decisions": [
            {
                "id": decision.id,
                "candidate_key": decision.pair_key,
                "left_name": decision.left_name,
                "right_name": decision.right_name,
                "canonical_name": decision.canonical_name,
                "decision": decision.decision,
                "created_at": decision.created_at,
                "updated_at": decision.updated_at,
            }
            for decision in person_identity_decisions
        ],
        "related_entities": related_projection,
        "events": events,
        "phases": phases,
        "narrative_status": narrative_status,
        "story_overview": story_overview,
        "character_relations": character_relations,
        "event_relations": event_relations,
        "deep_status": deep_status,
        "deep_analysis": deep_payload,
        "deep_revision": deep_revision,
        "chapters": [
            {"ordinal": chapter.ordinal, "title": chapter.title}
            for chapter in chapters
        ],
    }
    if include_question_evidence:
        character_design_status, character_design_evidence = (
            build_character_design_projection(session, run_id, projection)
        )
    else:
        character_design_status, character_design_evidence = "NOT_GENERATED", None
    projection["character_design_status"] = character_design_status
    projection["character_design_evidence"] = character_design_evidence
    if include_question_evidence:
        (
            opening_payoff_candidates_status,
            opening_payoff_candidates_evidence,
        ) = build_opening_payoff_candidates_projection(
            session,
            run_id,
            projection,
        )
    else:
        (
            opening_payoff_candidates_status,
            opening_payoff_candidates_evidence,
        ) = ("NOT_GENERATED", None)
    projection["opening_payoff_candidates_status"] = (
        opening_payoff_candidates_status
    )
    projection["opening_payoff_candidates_evidence"] = (
        opening_payoff_candidates_evidence
    )
    if include_question_evidence:
        chapter_end_hooks_status, chapter_end_hooks_evidence = (
            build_chapter_end_hooks_projection(session, run_id, projection)
        )
    else:
        chapter_end_hooks_status, chapter_end_hooks_evidence = "NOT_GENERATED", None
    projection["chapter_end_hooks_status"] = chapter_end_hooks_status
    projection["chapter_end_hooks_evidence"] = chapter_end_hooks_evidence
    projection["chapter_end_hooks"] = (
        chapter_end_hooks_evidence.get("chapters", [])
        if chapter_end_hooks_evidence
        else []
    )
    if include_question_evidence:
        opening_hook_payoffs_status, opening_hook_payoffs_evidence = (
            build_opening_hook_payoffs_projection(
                session,
                run_id,
                projection,
                hook_evidence=chapter_end_hooks_evidence,
            )
        )
    else:
        opening_hook_payoffs_status, opening_hook_payoffs_evidence = (
            "NOT_GENERATED",
            None,
        )
    projection["opening_hook_payoffs_status"] = opening_hook_payoffs_status
    projection["opening_hook_payoffs_evidence"] = opening_hook_payoffs_evidence
    projection["opening_hook_payoffs"] = (
        opening_hook_payoffs_evidence.get("hooks", [])
        if opening_hook_payoffs_evidence
        else []
    )
    if include_question_evidence:
        opening_structure_status, opening_structure_evidence = (
            build_opening_structure_projection(
                session,
                run_id,
                projection,
            )
        )
    else:
        opening_structure_status, opening_structure_evidence = (
            "NOT_GENERATED",
            None,
        )
    projection["opening_structure_status"] = opening_structure_status
    projection["opening_structure_evidence"] = opening_structure_evidence
    learning_readiness = assess_learning_report_readiness(projection)
    learning_report_status, learning_report = build_learning_report_projection(
        session,
        run_id,
        latest_deep_revision=deep_revision,
        readiness=learning_readiness,
    )
    projection["learning_report_status"] = learning_report_status
    projection["learning_report"] = learning_report
    learning_report["readiness"] = learning_readiness
    return projection


def build_state_at_chapter_projection(
    session: Session,
    run_id: str,
    chapter_ordinal: int,
    *,
    deep_revision: int | None = None,
) -> dict:
    """Replay the authoritative deep-analysis projection at one chapter."""
    projection = build_workbench_projection(
        session,
        run_id,
        deep_revision=deep_revision,
    )
    chapters = projection["chapters"]
    chapter = next(
        (item for item in chapters if item["ordinal"] == chapter_ordinal),
        None,
    )
    if chapter is None:
        raise ValueError("CHAPTER_ORDINAL_INVALID")

    deep = projection.get("deep_analysis") or {}
    facts = [
        item
        for item in deep.get("fact_versions", [])
        if int(item.get("valid_from_chapter") or 1) <= chapter_ordinal
        and (
            item.get("valid_to_chapter") is None
            or int(item["valid_to_chapter"]) >= chapter_ordinal
        )
    ]
    facts.sort(key=lambda item: (item.get("subject", ""), item.get("predicate", ""), item.get("id", "")))

    state_by_key: dict[tuple[str, str], dict] = {}
    for item in deep.get("state_changes", []):
        item_chapter = int(item.get("chapter_ordinal") or 0)
        if item_chapter > chapter_ordinal:
            continue
        key = (_normalize(item.get("subject", "")), _normalize(item.get("aspect", "")))
        current = state_by_key.get(key)
        if current is None or (
            item_chapter,
            str(item.get("id") or ""),
        ) >= (
            int(current.get("chapter_ordinal") or 0),
            str(current.get("id") or ""),
        ):
            state_by_key[key] = item

    knowledge_by_key: dict[tuple[str, str], dict] = {}
    for item in deep.get("actor_knowledge", []):
        item_chapter = int(item.get("chapter_ordinal") or 0)
        if item_chapter > chapter_ordinal:
            continue
        key = (_normalize(item.get("actor", "")), _normalize(item.get("proposition", "")))
        current = knowledge_by_key.get(key)
        if current is None or (
            item_chapter,
            str(item.get("id") or ""),
        ) >= (
            int(current.get("chapter_ordinal") or 0),
            str(current.get("id") or ""),
        ):
            knowledge_by_key[key] = item

    knowledge_transfers = [
        item
        for item in deep.get("knowledge_transfers", [])
        if int(item.get("chapter_ordinal") or 0) <= chapter_ordinal
    ]
    knowledge_transfers.sort(
        key=lambda item: (
            int(item.get("chapter_ordinal") or 0),
            item.get("target_actor", ""),
            item.get("proposition", ""),
            item.get("id", ""),
        )
    )

    rules = [
        item
        for item in deep.get("world_rules", [])
        if int(item.get("discovered_chapter") or 1) <= chapter_ordinal
    ]
    rules.sort(key=lambda item: (int(item.get("discovered_chapter") or 1), item.get("title", "")))
    return {
        "run_id": run_id,
        "deep_revision": projection.get("deep_revision"),
        "chapter_ordinal": chapter_ordinal,
        "chapter_title": chapter["title"],
        "facts": facts,
        "states": sorted(
            state_by_key.values(),
            key=lambda item: (item.get("subject", ""), item.get("aspect", "")),
        ),
        "knowledge": sorted(
            knowledge_by_key.values(),
            key=lambda item: (item.get("actor", ""), item.get("proposition", "")),
        ),
        "knowledge_transfers": knowledge_transfers,
        "world_rules": rules,
    }
