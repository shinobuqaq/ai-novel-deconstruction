from __future__ import annotations

from fastapi import APIRouter

from .routers.analysis import router as analysis_router
from .routers.common import (
    _analysis_issue_read,
    _analysis_profile_read,
    _analysis_run_diagnostics,
    _analysis_run_read,
    _artifact_read,
    _as_utc,
    _entity_candidate_read,
    _event_candidate_read,
    _json_dict,
    _model_service_read,
    _model_settings_error,
    _source_error,
    _source_issue_read,
    _source_structure_read,
    _task_read,
    _workbench_target_ids,
    _workspace_diagnostic_text,
)
from .routers.learning import router as learning_router
from .routers.projects import router as projects_router
from .routers.settings import router as settings_router
from .routers.sources import router as sources_router
from .routers.tasks import router as tasks_router
from .services.provider_config import probe_selected_model

router = APIRouter()
router.include_router(settings_router)
router.include_router(projects_router)
router.include_router(sources_router)
router.include_router(analysis_router)
router.include_router(learning_router)
router.include_router(tasks_router)

__all__ = [
    "router",
    "probe_selected_model",
    "_analysis_issue_read",
    "_analysis_profile_read",
    "_analysis_run_diagnostics",
    "_analysis_run_read",
    "_artifact_read",
    "_as_utc",
    "_entity_candidate_read",
    "_event_candidate_read",
    "_json_dict",
    "_model_service_read",
    "_model_settings_error",
    "_source_error",
    "_source_issue_read",
    "_source_structure_read",
    "_task_read",
    "_workbench_target_ids",
    "_workspace_diagnostic_text",
]
