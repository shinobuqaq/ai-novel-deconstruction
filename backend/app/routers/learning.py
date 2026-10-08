from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AnalysisRun, AnalysisRunTask, Task, TaskStatus
from ..schemas import AnalysisRunRead
from ..services.learning_report import (
    LearningReportNotReadyError,
    enqueue_learning_report,
)
from .common import _analysis_run_read

router = APIRouter()


@router.post(
    "/api/analysis-runs/{run_id}/learning-report/start",
    response_model=AnalysisRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def learning_report_start(
    run_id: str,
    request: Request,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    active_deep = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == "analysis.deep_insights",
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
    )
    if active_deep is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEEP_ANALYSIS_RUNNING",
                "message": "事实状态和核心拆解仍在生成，请完成后再生成创作学习报告。",
            },
        )
    try:
        task = enqueue_learning_report(session, request.app.state.settings, run)
    except LearningReportNotReadyError as exc:
        readiness = exc.readiness
        next_artifacts = readiness.get("next_required_artifacts", [])
        next_text = "、".join(str(item) for item in next_artifacts[:3])
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LEARNING_REPORT_DATA_NOT_READY",
                "message": (
                    f"当前拆书原料只支持 {readiness['ready_question_count']}/"
                    f"{readiness['total_question_count']} 个候选问题。系统没有创建在线任务，"
                    f"也不会消耗 Token（令牌）。下一步先补：{next_text}。"
                ),
                "readiness": readiness,
            },
        ) from exc
    if task is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEEP_ANALYSIS_NOT_READY",
                "message": "事实状态和核心拆解尚未完成，暂时不能生成创作学习报告。",
            },
        )
    return _analysis_run_read(session, run)
