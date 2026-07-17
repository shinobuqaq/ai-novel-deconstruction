from __future__ import annotations

import argparse
import socket
import threading
import time
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .db import create_db_engine, create_session_factory
from .models import Base
from .repositories import (
    ClaimedTask,
    claim_next_task,
    get_task,
    heartbeat_task,
    reap_expired_tasks,
)
from .services.tasks import execute_task_sync


def _maintain_lease(
    *,
    session_factory: sessionmaker[Session],
    claim: ClaimedTask,
    lease_seconds: int,
    stop: threading.Event,
    lease_lost: threading.Event,
    heartbeat_interval_seconds: float | None = None,
) -> None:
    interval = heartbeat_interval_seconds or max(0.1, lease_seconds / 3)
    while not stop.wait(interval):
        with session_factory() as session:
            renewed = heartbeat_task(
                session,
                task_id=claim.id,
                attempt_id=claim.current_attempt_id,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                worker_id=claim.lease_owner,
                lease_seconds=lease_seconds,
            )
        if not renewed:
            lease_lost.set()
            return


def run_worker(*, once: bool = False) -> None:
    settings = get_settings()
    settings.ensure_directories()
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    worker_id = f"{socket.gethostname()}-{uuid4().hex[:8]}"
    print(f"[worker] started id={worker_id}")

    while True:
        with session_factory() as session:
            reaped = reap_expired_tasks(session)
            if reaped:
                print(f"[worker] recovered expired tasks={reaped}")
            claim = claim_next_task(
                session,
                worker_id=worker_id,
                lease_seconds=settings.worker_lease_seconds,
            )

        if claim is not None:
            print(
                f"[worker] claimed task={claim.id} kind={claim.kind} "
                f"attempt={claim.attempts}"
            )
            stop_heartbeat = threading.Event()
            lease_lost = threading.Event()
            heartbeat = threading.Thread(
                target=_maintain_lease,
                kwargs={
                    "session_factory": session_factory,
                    "claim": claim,
                    "lease_seconds": settings.worker_lease_seconds,
                    "stop": stop_heartbeat,
                    "lease_lost": lease_lost,
                },
                name=f"heartbeat-{claim.id}",
                daemon=True,
            )
            heartbeat.start()
            accepted = execute_task_sync(session_factory, settings, claim)
            stop_heartbeat.set()
            heartbeat.join(timeout=max(1.0, settings.worker_lease_seconds))

            with session_factory() as session:
                persisted = get_task(session, claim.id)
                status = "MISSING" if persisted is None else persisted.status

            if not accepted and status == "CANCELLED":
                print(f"[worker] cancelled task={claim.id}")
            elif not accepted:
                print(
                    f"[worker] result rejected task={claim.id} "
                    f"reason=LEASE_LOST status={status}"
                )
            else:
                print(f"[worker] finished task={claim.id} status={status}")
        elif once:
            print("[worker] no pending task")

        if once:
            return
        time.sleep(settings.worker_poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Single-machine polling worker")
    parser.add_argument("--once", action="store_true", help="Process at most one task and exit")
    args = parser.parse_args()
    run_worker(once=args.once)


if __name__ == "__main__":
    main()
