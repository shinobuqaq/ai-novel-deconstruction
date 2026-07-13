from __future__ import annotations

import argparse
import socket
import time
from uuid import uuid4

from .config import get_settings
from .db import create_db_engine, create_session_factory
from .models import Base
from .repositories import claim_next_task
from .services.tasks import execute_task_sync


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
            task = claim_next_task(
                session,
                worker_id=worker_id,
                lease_seconds=settings.worker_lease_seconds,
            )
            if task is not None:
                print(f"[worker] claimed task={task.id} kind={task.kind} attempt={task.attempts}")
                execute_task_sync(session, settings, task)
                print(f"[worker] finished task={task.id} status={task.status}")
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
