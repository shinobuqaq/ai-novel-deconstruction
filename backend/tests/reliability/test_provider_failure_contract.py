from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.models import TaskStatus
from app.providers.base import ProviderError, ProviderResponse
from app.providers.fake import FakeProvider, FakeProviderMode
from app.providers.registry import ProviderRegistry
from app.repositories import claim_next_task, get_task
from app.services.tasks import execute_task_sync


@pytest.mark.parametrize(
    ("mode", "code", "retryable"),
    (
        (FakeProviderMode.TIMEOUT, "PROVIDER_TIMEOUT", True),
        (FakeProviderMode.RATE_LIMIT, "PROVIDER_RATE_LIMITED", True),
        (
            FakeProviderMode.TEMPORARY_UNAVAILABLE,
            "PROVIDER_UNAVAILABLE",
            True,
        ),
        (FakeProviderMode.INVALID_OUTPUT, "PROVIDER_INVALID_OUTPUT", True),
        (FakeProviderMode.AUTH_FAILED, "PROVIDER_AUTH_FAILED", False),
        (FakeProviderMode.BAD_REQUEST, "PROVIDER_BAD_REQUEST", False),
        (
            FakeProviderMode.PERMANENT_ERROR,
            "PROVIDER_PERMANENT_ERROR",
            False,
        ),
    ),
)
def test_fake_provider_has_stable_failure_contract(
    mode: FakeProviderMode,
    code: str,
    retryable: bool,
) -> None:
    provider = FakeProvider(mode=mode, retry_after_seconds=17)

    with pytest.raises(ProviderError) as caught:
        asyncio.run(
            provider.complete(task_kind="fake.echo", payload={"message": "x"})
        )

    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert caught.value.retry_after_seconds == (
        17 if mode == FakeProviderMode.RATE_LIMIT else None
    )


@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_code"),
    (
        (
            FakeProviderMode.TIMEOUT,
            TaskStatus.RETRY_WAIT.value,
            "PROVIDER_TIMEOUT",
        ),
        (
            FakeProviderMode.RATE_LIMIT,
            TaskStatus.RETRY_WAIT.value,
            "PROVIDER_RATE_LIMITED",
        ),
        (
            FakeProviderMode.AUTH_FAILED,
            TaskStatus.FAILED.value,
            "PROVIDER_AUTH_FAILED",
        ),
        (
            FakeProviderMode.BAD_REQUEST,
            TaskStatus.FAILED.value,
            "PROVIDER_BAD_REQUEST",
        ),
    ),
)
def test_provider_failure_classification_updates_task_state(
    reliability_env,
    task_factory,
    mode: FakeProviderMode,
    expected_status: str,
    expected_code: str,
) -> None:
    task_id = task_factory(max_attempts=3)
    with reliability_env.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id=f"worker-{mode}",
            lease_seconds=60,
        )
        assert claim is not None

    registry = ProviderRegistry(
        [FakeProvider(mode=mode, retry_after_seconds=17)]
    )
    before = datetime.now(timezone.utc)
    assert execute_task_sync(
        reliability_env.session_factory,
        reliability_env.settings,
        claim,
        registry,
    )

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == expected_status
        assert persisted.error_code == expected_code
        assert persisted.current_attempt is not None
        assert persisted.current_attempt.provider_name == "fake"
        assert persisted.current_attempt.error_code == expected_code
        if mode == FakeProviderMode.RATE_LIMIT:
            assert persisted.next_attempt_at is not None
            retry_at = persisted.next_attempt_at
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            assert retry_at >= before.replace(microsecond=0)


class InjectedProvider:
    name = "fake"

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        return ProviderResponse(
            raw_text='{"source":"injected"}',
            parsed={"source": "injected", "payload": payload},
            prompt_tokens=3,
            completion_tokens=4,
        )


def test_task_execution_uses_injected_provider(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory(payload={"message": "injected"})
    with reliability_env.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="worker-injected-provider",
            lease_seconds=60,
        )
        assert claim is not None

    assert execute_task_sync(
        reliability_env.session_factory,
        reliability_env.settings,
        claim,
        ProviderRegistry([InjectedProvider()]),
    )

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.SUCCEEDED.value
        assert persisted.current_attempt is not None
        assert persisted.current_attempt.provider_name == "fake"
        assert persisted.current_attempt.usage_json == (
            '{"completion_tokens": 4, "prompt_tokens": 3}'
        )


class InvalidOutputProvider:
    name = "fake"

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        return ProviderResponse(
            raw_text="not-json",
            parsed="not-an-object",  # type: ignore[arg-type]
            prompt_tokens=1,
            completion_tokens=1,
        )


class CrashingProvider:
    name = "fake"

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        raise RuntimeError("provider adapter crashed")


def test_unexpected_provider_exception_has_stable_error_code(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory()
    with reliability_env.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="worker-provider-crash",
            lease_seconds=60,
        )
        assert claim is not None

    assert execute_task_sync(
        reliability_env.session_factory,
        reliability_env.settings,
        claim,
        ProviderRegistry([CrashingProvider()]),
    )

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.FAILED.value
        assert persisted.error_code == "PROVIDER_UNEXPECTED_ERROR"


def test_invalid_provider_output_is_retryable(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory()
    with reliability_env.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="worker-invalid-output",
            lease_seconds=60,
        )
        assert claim is not None

    assert execute_task_sync(
        reliability_env.session_factory,
        reliability_env.settings,
        claim,
        ProviderRegistry([InvalidOutputProvider()]),
    )

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.RETRY_WAIT.value
        assert persisted.error_code == "PROVIDER_INVALID_OUTPUT"


def test_missing_provider_is_a_permanent_configuration_error() -> None:
    registry = ProviderRegistry()

    with pytest.raises(ProviderError) as caught:
        registry.resolve("missing")

    assert caught.value.code == "PROVIDER_NOT_CONFIGURED"
    assert not caught.value.retryable
