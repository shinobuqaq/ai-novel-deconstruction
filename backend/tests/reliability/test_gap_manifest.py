from __future__ import annotations

import re
from pathlib import Path


EXPECTED_GAP_IDS = {
    "M0-GAP-ARTIFACT-01",
    "M0-GAP-ARTIFACT-02",
    "M0-GAP-CANCEL-01",
    "M0-GAP-CANCEL-02",
    "M0-GAP-CANCEL-03",
    "M0-GAP-CLAIM-01",
    "M0-GAP-CLAIM-02",
    "M0-GAP-LEASE-01",
    "M0-GAP-LEASE-02",
    "M0-GAP-PROVIDER-01",
    "M0-GAP-REAPER-01",
    "M0-GAP-REAPER-02",
    "M0-GAP-RETRY-01",
    "M0-GAP-RETRY-02",
    "M0-GAP-RETRY-03",
}

GAP_ID_PATTERN = re.compile(r"M0-GAP-[A-Z]+-\d{2}")


def test_reliability_gap_manifest_matches_strict_xfails() -> None:
    reliability_dir = Path(__file__).parent
    observed: dict[str, str] = {}

    for path in sorted(reliability_dir.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue

        source = path.read_text(encoding="utf-8")
        gap_ids = GAP_ID_PATTERN.findall(source)
        xfail_count = source.count("@pytest.mark.xfail(")
        strict_count = source.count("strict=True,")

        assert xfail_count == strict_count, (
            f"{path.name} contains a non-strict or malformed xfail marker"
        )
        assert len(gap_ids) == xfail_count, (
            f"{path.name} must declare exactly one gap ID per xfail"
        )

        for gap_id in gap_ids:
            assert gap_id not in observed, (
                f"duplicate reliability gap ID {gap_id}: "
                f"{observed[gap_id]} and {path.name}"
            )
            observed[gap_id] = path.name

    assert set(observed) == EXPECTED_GAP_IDS
