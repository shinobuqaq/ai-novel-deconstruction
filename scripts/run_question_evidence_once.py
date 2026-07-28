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
from app.models import AnalysisRun, AnalysisRunTask, Task, TaskStatus  # noqa: E402
from app.providers import create_default_provider_registry  # noqa: E402
from app.repositories import claim_next_task, get_task  # noqa: E402
from app.services.chapter_end_hooks import (  # noqa: E402
    CHAPTER_END_HOOKS_TASK_KIND,
    enqueue_chapter_end_hooks,
)
from app.services.opening_hook_payoffs import (  # noqa: E402
    OPENING_HOOK_PAYOFFS_TASK_KIND,
    enqueue_opening_hook_payoffs,
)
from app.services.tasks import execute_task_sync  # noqa: E402


ACTIVE_STATUSES = {
    TaskStatus.PENDING.value,
    TaskStatus.RUNNING.value,
    TaskStatus.RETRY_WAIT.value,
    TaskStatus.WAITING_CONFIRMATION.value,
}


def _active_task_ids(
    session: Session,
    *,
    run_id: str,
    task_kind: str,
) -> list[str]:
    return list(session.scalars(
        select(Task.id)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == task_kind,
            Task.status.in_(ACTIVE_STATUSES),
        )
        .order_by(AnalysisRunTask.batch_index)
    ))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成指定北极星问题的专项证据账本，并顺序执行本批全部连续窗口。"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--question-id", required=True, choices=("3.4", "4.9"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_directories()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    task_kind = (
        OPENING_HOOK_PAYOFFS_TASK_KIND
        if args.question_id == "3.4"
        else CHAPTER_END_HOOKS_TASK_KIND
    )
    enqueue = (
        enqueue_opening_hook_payoffs
        if args.question_id == "3.4"
        else enqueue_chapter_end_hooks
    )
    try:
        with Session(engine) as session:
            run = session.get(AnalysisRun, args.run_id)
            if run is None:
                print("生成失败：找不到指定分析运行。", file=sys.stderr)
                return 2
            first_task = enqueue(session, settings, run, force=args.force)
            active_ids = _active_task_ids(
                session,
                run_id=run.id,
                task_kind=task_kind,
            )
        if first_task is None and not active_ids:
            print("没有创建任务：原料未就绪，或当前账本已经是最新版。")
            return 2

        print(
            f"开始生成北极星 {args.question_id}："
            f"本批 {len(active_ids)} 个连续窗口。"
        )
        registry = create_default_provider_registry(settings)
        completed_count = 0
        prompt_tokens = 0
        completion_tokens = 0
        while True:
            with Session(engine) as session:
                active_ids = _active_task_ids(
                    session,
                    run_id=args.run_id,
                    task_kind=task_kind,
                )
                if not active_ids:
                    break
                claim = claim_next_task(
                    session,
                    worker_id=f"question-{args.question_id}-one-shot",
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
            print(
                f"窗口 {task_payload.get('window_index', 1)}/"
                f"{task_payload.get('window_count', 1)}："
                f"第 {task_payload.get('chapter_start')}—"
                f"{task_payload.get('chapter_end')} 章"
            )
            accepted = execute_task_sync(
                session_factory,
                settings,
                claim,
                registry,
            )
            with Session(engine) as session:
                completed = get_task(session, claim.id)
                if completed is None:
                    print("执行失败：任务完成后无法读取。", file=sys.stderr)
                    return 2
                usage = (
                    json.loads(completed.current_attempt.usage_json)
                    if completed.current_attempt is not None
                    else {}
                )
                prompt_tokens += int(usage.get("prompt_tokens") or 0)
                completion_tokens += int(usage.get("completion_tokens") or 0)
                print(f"任务状态：{completed.status}")
                if completed.last_error_code or completed.last_error_message:
                    print(
                        f"失败信息：{completed.last_error_code or '未知错误'} - "
                        f"{completed.last_error_message or '没有错误详情'}"
                    )
            if not accepted or completed.status != TaskStatus.SUCCEEDED.value:
                return 1
            completed_count += 1

        print(
            f"北极星 {args.question_id} 完成：{completed_count} 个窗口；"
            f"Token（令牌）输入 {prompt_tokens}，输出 {completion_tokens}。"
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
