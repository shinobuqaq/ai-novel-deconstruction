from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.orm import Session


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import create_db_engine, create_session_factory  # noqa: E402
from app.models import AnalysisRun  # noqa: E402
from app.providers import create_default_provider_registry  # noqa: E402
from app.repositories import claim_next_task, get_task  # noqa: E402
from app.services.learning_report import enqueue_learning_report  # noqa: E402
from app.services.tasks import execute_task_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="为指定分析运行生成当前可答的创作学习问题，并等待本次任务完成。"
    )
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_directories()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        with Session(engine) as session:
            run = session.get(AnalysisRun, args.run_id)
            if run is None:
                print("生成失败：找不到指定分析运行。", file=sys.stderr)
                return 2
            task = enqueue_learning_report(session, settings, run)
        if task is None:
            print("生成失败：深层拆解尚未就绪。", file=sys.stderr)
            return 2

        with Session(engine) as session:
            persisted = get_task(session, task.id)
            if persisted is None:
                print("生成失败：任务创建后无法读取。", file=sys.stderr)
                return 2
            if persisted.status not in {"PENDING", "RETRY_WAIT"}:
                print(
                    f"没有新增问题需要生成；最近任务 {persisted.id} 状态为 {persisted.status}。"
                )
                return 0
            claim = claim_next_task(
                session,
                worker_id="learning-report-one-shot",
                lease_seconds=max(900, int(settings.openai_timeout_seconds) + 300),
            )
        if claim is None or claim.id != task.id:
            print(
                "生成失败：任务未被本次单次执行器取得，可能已有工作台执行器先领取。",
                file=sys.stderr,
            )
            return 3

        task_payload = json.loads(claim.payload_json)
        question_ids = task_payload.get("question_ids", [])
        print(f"开始生成问题：{', '.join(question_ids)}")
        accepted = execute_task_sync(
            session_factory,
            settings,
            claim,
            create_default_provider_registry(settings),
        )
        with Session(engine) as session:
            completed = get_task(session, claim.id)
            if completed is None:
                print("生成失败：任务完成后无法读取。", file=sys.stderr)
                return 2
            print(f"任务状态：{completed.status}")
            if completed.last_error_code or completed.last_error_message:
                print(
                    f"失败信息：{completed.last_error_code or '未知错误'} - "
                    f"{completed.last_error_message or '没有错误详情'}"
                )
            usage = (
                json.loads(completed.current_attempt.usage_json)
                if completed.current_attempt is not None
                else {}
            )
            if usage.get("prompt_tokens") is not None or usage.get("completion_tokens") is not None:
                print(
                    f"Token（令牌）：输入 {usage.get('prompt_tokens') or 0}，"
                    f"输出 {usage.get('completion_tokens') or 0}"
                )
        return 0 if accepted and completed.status == "SUCCEEDED" else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
