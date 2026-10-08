from __future__ import annotations

from typing import Any
from ..base import BaseQuestionPlugin
from ..schemas import LearningAnswerProposal


class Q3_4Plugin(BaseQuestionPlugin):
    question_id = "3.4"

    def assess_readiness(self, projection: dict[str, Any]) -> dict[str, object]:
        chapter_count = len(projection.get("chapters", []))
        opening_payoffs_evidence = (
            projection.get("opening_hook_payoffs_evidence") or {}
        )
        opening_hook_samples = (
            opening_payoffs_evidence.get("hooks", [])
            if isinstance(opening_payoffs_evidence, dict)
            else []
        )
        opening_coverage = (
            opening_payoffs_evidence.get("coverage", {})
            if isinstance(opening_payoffs_evidence, dict)
            else {}
        )
        opening_hook_gaps: list[str] = []
        required_opening_hook_count = min(3, chapter_count)
        if not opening_payoffs_evidence:
            opening_hook_gaps.append(
                "3.4 需要独立的前三章章末钩兑现追踪；4.9 只负责全书类型与节律，不能再拿它代替。"
            )
        elif opening_payoffs_evidence.get("is_current") is False:
            opening_hook_gaps.append(
                "3.4 兑现追踪对应旧版正文或旧版 4.9 账本，需要重新生成。"
            )
        if len(opening_hook_samples) < required_opening_hook_count:
            opening_hook_gaps.append(
                f"前三章需要 {required_opening_hook_count} 条钩子兑现记录，"
                f"当前只有 {len(opening_hook_samples)} 条。"
            )
        if chapter_count < 3:
            opening_hook_gaps.append(
                f"作品当前只有 {chapter_count} 章，无法形成完整三章口径。"
            )
        if (
            opening_payoffs_evidence
            and opening_coverage.get("search_policy")
            != "ALL_LATER_CHAPTERS_WINDOWED"
        ):
            opening_hook_gaps.append(
                "3.4 没有按连续窗口搜索全部后续章节，不能判定最早回应或全书未回应。"
            )
        if (
            opening_payoffs_evidence
            and opening_coverage.get("all_windows_completed") is not True
            and opening_coverage.get("all_required_hooks_resolved") is not True
        ):
            opening_hook_gaps.append(
                "3.4 尚未找到全部开篇钩子的首次回应，也没有连续检查到全书结尾。"
            )
        if (
            opening_payoffs_evidence
            and opening_coverage.get("ending_evidence_complete") is not True
        ):
            opening_hook_gaps.append("3.4 的前三章真实章末证据不完整。")
        if (
            opening_payoffs_evidence
            and opening_coverage.get("response_position_program_validated")
            is not True
        ):
            opening_hook_gaps.append("3.4 的回应章节与原文对应尚未通过程序校验。")

        ready = not opening_hook_gaps
        return {
            "question_id": self.question_id,
            "ready": ready,
            "observed": {
                "required_chapter_count": required_opening_hook_count,
                "classified_opening_chapter_count": len(opening_hook_samples),
                "valid_payoff_tracking_count": len(opening_hook_samples),
                "search_policy": opening_coverage.get("search_policy"),
                "window_count": opening_coverage.get("window_count"),
                "all_windows_completed": opening_coverage.get(
                    "all_windows_completed"
                ),
            },
            "gaps": opening_hook_gaps,
            "required_artifact": "前三章章末钩兑现追踪表（独立于 4.9）",
            "answer_scope": "COMPLETE" if ready else "NOT_READY",
            "source_material": opening_payoffs_evidence,
        }

    def validate_answer(
        self,
        answer: LearningAnswerProposal,
        projection: dict[str, Any],
        errors: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        return
