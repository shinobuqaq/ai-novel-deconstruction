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
    SourceUnit,
    Task,
    TaskStatus,
)


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

    person_names: set[str] = set()
    person_by_name: dict[str, EntityCandidate] = {}
    for entity in entities:
        if entity.entity_type != "PERSON":
            continue
        names = [entity.name, *_read_json(entity.aliases_json)]
        for name in names:
            normalized = _normalize(name)
            if normalized:
                person_names.add(normalized)
                person_by_name.setdefault(normalized, entity)

    related_entities = [entity for entity in entities if entity.entity_type != "PERSON"]

    def event_chapters(event: EventCandidate) -> list[_ChapterRef]:
        spans = [evidence_by_id[item] for item in _read_json(event.evidence_ids_json) if item in evidence_by_id]
        if spans:
            start = min(item.start_char for item in spans)
            end = max(item.end_char for item in spans)
        else:
            start, end = event.start_char, event.end_char
        return _chapters_for_range(chapters, start, max(start + 1, end))

    # Only exact normalized title/type matches are merged here. The projection is
    # intentionally conservative; semantic merge suggestions belong in the next
    # review stage and must remain reversible.
    grouped_events: dict[tuple[str, str], list[EventCandidate]] = {}
    for event in event_candidates:
        grouped_events.setdefault((_normalize(event.title), event.event_type), []).append(event)

    events: list[dict] = []
    event_id_by_candidate: dict[str, str] = {}
    for (normalized_title, event_type), group in grouped_events.items():
        group = sorted(group, key=lambda item: (item.start_char, item.id))
        canonical_id = f"cev_{_hash(f'{run_id}:{normalized_title}:{event_type}')[:32]}"
        evidence_ids = _unique([item for event in group for item in _read_json(event.evidence_ids_json)])
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
        })
    events.sort(key=lambda item: (item["start_char"], item["title"]))

    characters: list[dict] = []
    for entity in sorted((item for item in entities if item.entity_type == "PERSON"), key=lambda item: item.name):
        evidence_ids = _read_json(entity.evidence_ids_json)
        refs = [
            evidence_by_id[item]
            for item in evidence_ids
            if item in evidence_by_id
        ]
        character_event_ids = [
            event["id"]
            for event in events
            if any(_normalize(person) in {_normalize(entity.name), *(_normalize(alias) for alias in _read_json(entity.aliases_json))} for person in event["people"])
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
            "aliases": _read_json(entity.aliases_json),
            "description": entity.description,
            "evidence_ids": evidence_ids,
            "event_ids": character_event_ids,
            "first_chapter_ordinal": chapter_refs[0].ordinal if chapter_refs else None,
            "first_chapter_title": chapter_refs[0].title if chapter_refs else None,
            "last_chapter_ordinal": chapter_refs[-1].ordinal if chapter_refs else None,
            "last_chapter_title": chapter_refs[-1].title if chapter_refs else None,
            "appearance_count": appearance_count,
            "activity_level": activity_level,
            "status": entity.status,
            "confidence": entity.confidence,
        })

    synthesis = session.scalar(
        select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run_id)
    ) if include_synthesis else None
    deep_analysis = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run_id)
        .order_by(DeepAnalysis.revision_no.desc())
    ) if include_synthesis else None
    narrative_status = "NOT_GENERATED"
    story_overview = None
    character_relations: list[dict] = []
    event_relations: list[dict] = []
    phases: list[dict] = []
    role_by_name: dict[str, dict] = {}
    if synthesis is not None:
        narrative_status = "READY"
        payload = json.loads(synthesis.payload_json)
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
            phase_event_ids = [
                event_id for event_id in phase.get("event_ids", []) if event_id in event_by_id
            ]
            phase_events = [event_by_id[event_id] for event_id in phase_event_ids]
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
            phases.append({
                "id": f"phs_{_hash(f'{run_id}:{index}:{','.join(phase_event_ids)}')[:32]}",
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
                "evidence_ids": phase.get("evidence_ids", []),
                "chapter_ordinals": chapter_ordinals,
                "chapter_titles": chapter_titles,
                "people": _unique([person for event in phase_events for person in event["people"]]),
            })
        for event in event_relations:
            if event.get("source_event_id") in event_by_id and event.get("target_event_id") in event_by_id:
                event["source_title"] = event_by_id[event["source_event_id"]]["title"]
                event["target_title"] = event_by_id[event["target_event_id"]]["title"]

    deep_status = "NOT_GENERATED"
    deep_payload = None
    deep_revision = None
    if deep_analysis is not None:
        deep_status = "READY"
        deep_payload = json.loads(deep_analysis.payload_json)
        deep_revision = deep_analysis.revision_no

    for character in characters:
        role = role_by_name.get(_normalize(character["name"]))
        character.update({
            "role": role.get("role", "UNCLASSIFIED") if role else "UNCLASSIFIED",
            "role_reason": role.get("role_reason", "尚未完成角色定位") if role else "尚未完成角色定位",
            "goals": role.get("goals", []) if role else [],
            "motivations": role.get("motivations", []) if role else [],
            "current_state": role.get("current_state", "") if role else "",
        })

    return {
        "run_id": run_id,
        "source_version_id": run.source_version_id,
        "status": run.status,
        "characters": characters,
        "related_entities": [
            {
                "id": entity.id,
                "run_id": entity.run_id,
                "source_version_id": entity.source_version_id,
                "name": entity.name,
                "entity_type": entity.entity_type,
                "aliases": _read_json(entity.aliases_json),
                "description": entity.description,
                "evidence_ids": _read_json(entity.evidence_ids_json),
                "status": entity.status,
                "confidence": entity.confidence,
            }
            for entity in sorted(related_entities, key=lambda item: (item.entity_type, item.name))
        ],
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
