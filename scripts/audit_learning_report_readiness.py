from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.models import AnalysisRun, DeepAnalysis, SourceVersion  # noqa: E402
from app.services.workbench import build_workbench_projection  # noqa: E402


def _database_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.resolve().as_posix()}"


def _select_run(session: Session, run_id: str | None) -> AnalysisRun:
    if run_id:
        run = session.get(AnalysisRun, run_id)
    else:
        run = session.scalar(
            select(AnalysisRun)
            .join(SourceVersion, SourceVersion.id == AnalysisRun.source_version_id)
            .join(DeepAnalysis, DeepAnalysis.run_id == AnalysisRun.id)
            .order_by(SourceVersion.total_chars.desc(), AnalysisRun.created_at.desc())
        )
    if run is None:
        raise RuntimeError("没有找到带深层拆解结果的分析运行。")
    return run


def audit(database: Path, run_id: str | None) -> dict[str, object]:
    if not database.is_file():
        raise RuntimeError(f"数据库不存在：{database.resolve()}")
    engine = create_engine(_database_url(database), future=True)
    try:
        with Session(engine) as session:
            run = _select_run(session, run_id)
            version = session.get(SourceVersion, run.source_version_id)
            if version is None:
                raise RuntimeError("分析运行引用的来源版本不存在。")
            projection = build_workbench_projection(session, run.id)
            readiness = projection["learning_report"]["readiness"]
            return {
                "database": str(database.resolve()),
                "run_id": run.id,
                "run_status": run.status,
                "source_version_id": version.id,
                "source_chars": version.total_chars,
                "chapter_count": version.chapter_count,
                "readiness": readiness,
            }
    finally:
        engine.dispose()


def _print_human(report: dict[str, object]) -> None:
    readiness = report["readiness"]
    assert isinstance(readiness, dict)
    print(
        f"运行 {report['run_id']}：{report['source_chars']:,} 字符，"
        f"{report['chapter_count']} 章，状态 {report['run_status']}。"
    )
    print(
        f"核心 42 问中，首组已审计 {readiness['assessed_question_count']} 问："
        f"{readiness['complete_question_count']} 问可完整回答，"
        f"{readiness['partial_question_count']} 问可部分回答；"
        f"当前可增量生成：{'是' if readiness['ready'] else '否'}。"
    )
    for item in readiness["checks"]:
        status = {
            "COMPLETE": "可完整回答",
            "PARTIAL": "可部分回答",
            "NOT_READY": "原料未就绪",
        }.get(item["answer_scope"], "状态未知")
        print(f"- {item['question_id']} {status}：{item['required_artifact']}")
        for gap in item["gaps"]:
            print(f"  限制或缺口：{gap}")
    if readiness["next_required_artifacts"]:
        print("首组尚需补的专项原料：")
        for artifact in readiness["next_required_artifacts"]:
            print(f"- {artifact}")
    print("本审计只读数据库，不创建在线任务，不消耗 Token（令牌）。")


def main() -> int:
    parser = argparse.ArgumentParser(description="审计创作学习报告的数据就绪状态。")
    parser.add_argument(
        "--database",
        type=Path,
        default=REPO_ROOT / "workspace" / "app.db",
        help="SQLite 数据库路径，默认使用正式工作区数据库。",
    )
    parser.add_argument("--run-id", help="指定分析运行；省略时选择字符数最多且已有深层结果的运行。")
    parser.add_argument("--json", action="store_true", help="输出 JSON，供自动检查使用。")
    args = parser.parse_args()
    try:
        report = audit(args.database, args.run_id)
    except RuntimeError as exc:
        print(f"审计失败：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
