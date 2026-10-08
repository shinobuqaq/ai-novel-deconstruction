from app.services.analysis import _claim_verification


def test_pattern_claim_needs_more_than_one_supporting_span() -> None:
    status, note = _claim_verification({
        "claim_kind": "PATTERN",
        "evidence_ids": ["e1"],
        "counter_evidence_ids": [],
        "confidence": 95,
    })

    assert status == "INSUFFICIENT"
    assert "至少需要 2 条" in note


def test_counter_evidence_prevents_supported_publication() -> None:
    status, _note = _claim_verification({
        "claim_kind": "FACT",
        "evidence_ids": ["e1"],
        "counter_evidence_ids": ["e2"],
        "confidence": 95,
    })

    assert status == "DISPUTED"


def test_low_confidence_claim_is_only_partially_supported() -> None:
    status, _note = _claim_verification({
        "claim_kind": "INFERENCE",
        "evidence_ids": ["e1"],
        "counter_evidence_ids": [],
        "confidence": 60,
    })

    assert status == "PARTIAL"
