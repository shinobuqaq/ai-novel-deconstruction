from __future__ import annotations

import sys
from fastapi import APIRouter, HTTPException, Request, status

from ..schemas import (
    AnalysisProfileRead,
    AnalysisProfileWrite,
    ModelCatalogRead,
    ModelConnectionRead,
    ModelProbeRead,
    ModelServiceRead,
    ModelServiceWrite,
    ModelSettingsRead,
    OpenAIConfigRead,
    OpenAIConfigWrite,
)
from ..services.provider_config import (
    ModelSettingsError,
    delete_model_service,
    discover_models,
    probe_selected_model,
    read_model_settings,
    read_openai_config,
    record_connection_result,
    save_analysis_profile,
    save_model_service,
    write_openai_config,
)
from .common import (
    _analysis_profile_read,
    _model_service_read,
    _model_settings_error,
)

router = APIRouter()


def _get_probe_fn():
    api_mod = sys.modules.get("app.api")
    if api_mod is not None and hasattr(api_mod, "probe_selected_model"):
        return api_mod.probe_selected_model
    return probe_selected_model


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    return {"status": "ok", "app": request.app.title}


@router.get("/api/settings/openai", response_model=OpenAIConfigRead)
def openai_config_get(request: Request) -> OpenAIConfigRead:
    config = read_openai_config(request.app.state.settings)
    return OpenAIConfigRead(
        configured=config.configured,
        base_url=config.base_url,
        model=config.model,
    )


@router.put("/api/settings/openai", response_model=OpenAIConfigRead)
def openai_config_put(
    payload: OpenAIConfigWrite,
    request: Request,
) -> OpenAIConfigRead:
    try:
        config = write_openai_config(
            request.app.state.settings,
            api_key=payload.api_key,
            base_url=payload.base_url,
            model=payload.model,
        )
    except ValueError as error:
        messages = {
            "OPENAI_API_KEY_REQUIRED": "请输入 API Key。",
            "OPENAI_BASE_URL_INVALID": "接口地址必须使用 HTTPS。",
            "OPENAI_MODEL_REQUIRED": "模型名称不能为空。",
        }
        raise HTTPException(
            status_code=422,
            detail={"code": str(error), "message": messages.get(str(error), "AI 配置无效。")},
        ) from error
    return OpenAIConfigRead(
        configured=config.configured,
        base_url=config.base_url,
        model=config.model,
    )


@router.get("/api/settings/models", response_model=ModelSettingsRead)
def model_settings_get(request: Request) -> ModelSettingsRead:
    settings = read_model_settings(request.app.state.settings)
    return ModelSettingsRead(
        services=[_model_service_read(item) for item in settings.services],
        analysis_profiles=[_analysis_profile_read(item) for item in settings.analysis_profiles],
    )


@router.post(
    "/api/settings/model-services",
    response_model=ModelServiceRead,
    status_code=status.HTTP_201_CREATED,
)
def model_services_create(payload: ModelServiceWrite, request: Request) -> ModelServiceRead:
    try:
        service = save_model_service(
            request.app.state.settings,
            service_id=None,
            name=payload.name,
            service_type=payload.service_type,
            base_url=payload.base_url,
            api_key=payload.api_key,
        )
    except ModelSettingsError as error:
        raise _model_settings_error(error) from error
    return _model_service_read(service)


@router.put(
    "/api/settings/model-services/{service_id}",
    response_model=ModelServiceRead,
)
def model_services_update(
    service_id: str,
    payload: ModelServiceWrite,
    request: Request,
) -> ModelServiceRead:
    try:
        service = save_model_service(
            request.app.state.settings,
            service_id=service_id,
            name=payload.name,
            service_type=payload.service_type,
            base_url=payload.base_url,
            api_key=payload.api_key,
        )
    except ModelSettingsError as error:
        raise _model_settings_error(error) from error
    return _model_service_read(service)


@router.delete(
    "/api/settings/model-services/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def model_services_delete(service_id: str, request: Request) -> None:
    try:
        delete_model_service(request.app.state.settings, service_id)
    except ModelSettingsError as error:
        raise _model_settings_error(error) from error


@router.get(
    "/api/settings/model-services/{service_id}/models",
    response_model=ModelCatalogRead,
)
async def model_services_models(service_id: str, request: Request) -> ModelCatalogRead:
    try:
        models = await discover_models(request.app.state.settings, service_id)
    except ModelSettingsError as error:
        try:
            record_connection_result(
                request.app.state.settings,
                service_id,
                success=False,
                message=error.message,
                model_catalog_status="UNSUPPORTED" if error.code in {
                    "PROVIDER_MODELS_UNSUPPORTED",
                    "PROVIDER_MODELS_INVALID",
                    "PROVIDER_MODELS_EMPTY",
                } else "FAILED",
            )
        except ModelSettingsError:
            pass
        raise _model_settings_error(error, connection=True) from error
    record_connection_result(
        request.app.state.settings,
        service_id,
        success=True,
        message=f"已读取 {len(models)} 个可用模型。",
        model_catalog_status="SUPPORTED",
    )
    return ModelCatalogRead(
        service_id=service_id,
        models=models,
        message=f"已读取 {len(models)} 个可用模型。",
    )


@router.post(
    "/api/settings/model-services/{service_id}/test",
    response_model=ModelConnectionRead,
)
async def model_services_test(service_id: str, request: Request) -> ModelConnectionRead:
    try:
        models = await discover_models(request.app.state.settings, service_id)
    except ModelSettingsError as error:
        if error.code in {
            "PROVIDER_MODELS_UNSUPPORTED",
            "PROVIDER_MODELS_INVALID",
            "PROVIDER_MODELS_EMPTY",
        }:
            service = record_connection_result(
                request.app.state.settings,
                service_id,
                success=True,
                message=error.message,
                model_catalog_status="UNSUPPORTED",
            )
            return ModelConnectionRead(
                service=_model_service_read(service),
                model_count=0,
                message=error.message,
            )
        try:
            record_connection_result(
                request.app.state.settings,
                service_id,
                success=False,
                message=error.message,
                model_catalog_status="FAILED",
            )
        except ModelSettingsError:
            pass
        raise _model_settings_error(error, connection=True) from error
    message = f"连接成功，并读取到 {len(models)} 个模型。"
    service = record_connection_result(
        request.app.state.settings,
        service_id,
        success=True,
        message=message,
        model_catalog_status="SUPPORTED",
    )
    return ModelConnectionRead(
        service=_model_service_read(service),
        model_count=len(models),
        message=message,
    )


@router.post(
    "/api/settings/analysis-profiles/{profile_id}/test",
    response_model=ModelProbeRead,
)
async def analysis_profile_test(profile_id: str, request: Request) -> ModelProbeRead:
    probe_fn = _get_probe_fn()
    try:
        result = await probe_fn(request.app.state.settings, profile_id)
    except ModelSettingsError as error:
        raise _model_settings_error(error, connection=True) from error
    return ModelProbeRead(
        service=_model_service_read(result.service),
        message=result.message,
    )


@router.put(
    "/api/settings/analysis-profiles/{profile_id}",
    response_model=AnalysisProfileRead,
)
def analysis_profiles_update(
    profile_id: str,
    payload: AnalysisProfileWrite,
    request: Request,
) -> AnalysisProfileRead:
    try:
        profile = save_analysis_profile(
            request.app.state.settings,
            profile_id=profile_id,
            name=payload.name,
            service_id=payload.service_id,
            model=payload.model,
            temperature=payload.temperature,
            max_output_tokens=payload.max_output_tokens,
            reasoning_effort=payload.reasoning_effort,
            timeout_seconds=payload.timeout_seconds,
            max_retries=payload.max_retries,
            context_window_tokens=payload.context_window_tokens,
            failover_targets=[item.model_dump() for item in payload.failover_targets],
        )
    except ModelSettingsError as error:
        raise _model_settings_error(error) from error
    return _analysis_profile_read(profile)
