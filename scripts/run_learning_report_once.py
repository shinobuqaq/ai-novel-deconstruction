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
from app.db import create_db_engine, create_session_factory  # noqa: E402
from app.models import (  # noqa: E402
    AnalysisRun,
    AnalysisRunTask,
    Task,
    TaskStatus,
)
from app.providers import create_default_provider_registry  # noqa: E402
from app.repositories import claim_next_task, get_task  # noqa: E402
from app.services.learning_report import (  # noqa: E402
    LEARNING_REPORT_TASK_KIND,
    LearningReportNotReadyError,
    enqueue_learning_report,
)
from app.services.opening_payoff_candidates import (  # noqa: E402
    OPENING_PAYOFF_CANDIDATES_TASK_KIND,
)
from app.services.tasks import execute_task_sync  # noqa: E402


ACTIVE_STATUSES = {
    TaskStatus.PENDING.value,
    TaskStatus.RUNNING.value,
    TaskStatus.RETRY_WAIT.value,
    TaskStatus.WAITING_CONFIRMATION.value,
}


def _active_pipeline_task_ids(
    session: Session,
    *,
    run_id: str,
    question_id: str | None,
) -> list[str]:
    tasks = list(session.scalars(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind.in_((
                LEARNING_REPORT_TASK_KIND,
                OPENING_PAYOFF_CANDIDATES_TASK_KIND,
            )),
            Task.status.in_(ACTIVE_STATUSES),
        )
        .order_by(AnalysisRunTask.batch_index)
    ))
    matched: list[str] = []
    for task in tasks:
        payload = json.loads(task.payload_json)
        if question_id is None:
            matched.append(task.id)
        elif (
            task.kind == OPENING_PAYOFF_CANDIDATES_TASK_KIND
            and question_id == "1.4"
        ):
            matched.append(task.id)
        elif (
            task.kind == LEARNING_REPORT_TASK_KIND
            and question_id in (payload.get("question_ids") or [])
        ):
            matched.append(task.id)
    return matched


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "为指定分析运行流式生成当前可答的创作学习问题；"
            "持续接收分片，流结束后再完成 JSON 与证据校验。"
        )
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--question-id",
        choices=("1.4", "2.1", "2.2", "3.4", "4.9"),
        help="只生成这一问；省略时按当前就绪与过期状态生成全部需要更新的问题。",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_directories()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        try:
            with Session(engine) as session:
                run = session.get(AnalysisRun, args.run_id)
                if run is None:
                    print("生成失败：找不到指定分析运行。", file=sys.stderr)
                    return 2
                enqueue_learning_report(
                    session,
                    settings,
                    run,
                    force=args.force,
                    only_question_ids=(
                        (args.question_id,) if args.question_id else None
                    ),
                )
        except LearningReportNotReadyError as exc:
            print(
                f"生成失败：{exc.readiness.get('summary') or '当前问题原料尚未就绪。'}",
                file=sys.stderr,
            )
            return 2

        registry = create_default_provider_registry(settings)
        completed_count = 0
        prompt_tokens = 0
        completion_tokens = 0
        while True:
            with Session(engine) as session:
                active_ids = _active_pipeline_task_ids(
                    session,
                    run_id=args.run_id,
                    question_id=args.question_id,
                )
                if not active_ids:
                    break
                claim = claim_next_task(
                    session,
                    worker_id="learning-report-one-shot",
                    lease_seconds=max(
                        900,
                        int(settings.openai_timeout_seconds) + 300,
                    ),
                )
            if claim is None or claim.id not in active_ids:
                print(
                    "执行暂停：本批任务当前不可领取，可能正在等待重试或被另一执行器领取。",
                    file=sys.stderr,
                )
                return 3
            task_payload = json.loads(claim.payload_json)
            if claim.kind == OPENING_PAYOFF_CANDIDATES_TASK_KIND:
                print(
                    "开始生成问题 1.4 的卖点首次兑现连续候选账本："
                    f"窗口 {task_payload.get('window_index', 1)}/"
                    f"{task_payload.get('window_count', 1)}"
                )
            else:
                question_ids = task_payload.get("question_ids", [])
                print(f"开始生成问题：{', '.join(question_ids)}")
            accepted = execute_task_sync(
                session_factory,
                settings,
                claim,
                registry,
            )
            with Session(engine) as session:
                completed = get_task(session, claim.id)
                if completed is None:
                    print("生成失败：任务完成后无法读取。", file=sys.stderr)
                    return 2
                usage = (
                    json.loads(completed.current_attempt.usage_json)
                    if completed.current_attempt is not None
                    else {}
                )
                prompt_tokens += int(usage.get("prompt_tokens") or 0)
                completion_tokens += int(
                    usage.get("completion_tokens") or 0
                )
                print(f"任务状态：{completed.status}")
                if completed.last_error_code or completed.last_error_message:
                    print(
                        f"失败信息：{completed.last_error_code or '未知错误'} - "
                        f"{completed.last_error_message or '没有错误详情'}"
                    )
            if not accepted or completed.status != TaskStatus.SUCCEEDED.value:
                return 1
            completed_count += 1

        if completed_count == 0:
            print("没有新增问题需要生成；当前答案已经是最新版。")
            return 0
        print(
            f"本轮完成 {completed_count} 个流式任务；"
            f"Token（令牌）输入 {prompt_tokens}，输出 {completion_tokens}。"
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
