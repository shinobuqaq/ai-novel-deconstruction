from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import create_db_engine  # noqa: E402
from app.models import (  # noqa: E402
    AnalysisRunTask,
    LearningReport,
    Task,
    TaskStatus,
)
from app.services.learning_report import (  # noqa: E402
    LEARNING_REPORT_PROGRAM_COMPILED_QUESTION_IDS,
    LearningAnswerProposal,
    LearningReportOutput,
    persist_learning_report,
)


def _task_question_ids(task: Task) -> list[str]:
    return [
        str(question_id)
        for question_id in json.loads(task.payload_json).get(
            "question_ids",
            [],
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "不调用模型，使用当前专项账本重新编译已成功任务的"
            "程序拥有学习答案。"
        )
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--question-id",
        required=True,
        choices=sorted(LEARNING_REPORT_PROGRAM_COMPILED_QUESTION_IDS),
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = create_db_engine(settings)
    try:
        with Session(engine) as session:
            tasks = list(session.scalars(
                select(Task)
                .join(
                    AnalysisRunTask,
                    AnalysisRunTask.task_id == Task.id,
                )
                .where(
                    AnalysisRunTask.run_id == args.run_id,
                    Task.kind == "analysis.learning_report",
                    Task.status == TaskStatus.SUCCEEDED.value,
                )
                .order_by(Task.created_at.desc())
            ))
            task = next(
                (
                    item
                    for item in tasks
                    if args.question_id in _task_question_ids(item)
                ),
                None,
            )
            if task is None:
                print(
                    "重新编译失败：找不到该问题已经成功的正式任务。",
                    file=sys.stderr,
                )
                return 2
            report = session.scalar(
                select(LearningReport).where(
                    LearningReport.created_by_task_id == task.id
                )
            )
            if report is None:
                print(
                    "重新编译失败：成功任务没有对应的正式学习报告。",
                    file=sys.stderr,
                )
                return 2
            payload = json.loads(report.payload_json)
            answer_payload = next(
                (
                    item
                    for item in payload.get("answers", [])
                    if item.get("question_id") == args.question_id
                ),
                None,
            )
            if answer_payload is None:
                print(
                    "重新编译失败：正式报告中找不到该问题答案。",
                    file=sys.stderr,
                )
                return 2
            allowed_fields = set(LearningAnswerProposal.model_fields)
            answer = LearningAnswerProposal.model_validate({
                key: value
                for key, value in answer_payload.items()
                if key in allowed_fields
            })
            persisted = persist_learning_report(
                session,
                settings=settings,
                task=task,
                attempt_id=task.current_attempt_id,
                task_payload=json.loads(task.payload_json),
                output=LearningReportOutput(
                    answers=[answer],
                    author_decisions=[],
                    method_candidates=[],
                ),
            )
            print(
                f"已离线重新编译问题 {args.question_id}；"
                f"正式报告 {persisted.report_id}，没有调用模型。"
            )
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
