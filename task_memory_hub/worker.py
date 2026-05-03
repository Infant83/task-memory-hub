from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .branding import APP_NAME
from .config import default_db_path
from .notification_adapters import DEFAULT_NOTIFICATION_CHANNEL, NotificationDispatchError, dispatch_notification
from .service import (
    claim_ready_notification_jobs,
    enqueue_due_notifications,
    ensure_db,
    record_notification_attempt,
)


def _db_file(db_path: str | Path | None = None) -> Path:
    return Path(db_path) if db_path else default_db_path()


def worker_pause_path(db_path: str | Path | None = None) -> Path:
    path = _db_file(db_path)
    return path.with_name(f"{path.name}.worker-paused")


def pause_worker(db_path: str | Path | None = None, reason: str = "manual") -> dict:
    ensure_db(db_path)
    marker = worker_pause_path(db_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {"paused": True, "reason": reason, "marker": str(marker)}
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return worker_status(db_path)


def resume_worker(db_path: str | Path | None = None) -> dict:
    ensure_db(db_path)
    marker = worker_pause_path(db_path)
    if marker.exists():
        marker.unlink()
    return worker_status(db_path)


def worker_status(db_path: str | Path | None = None) -> dict:
    marker = worker_pause_path(db_path)
    payload = {}
    if marker.exists():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"paused": True, "reason": "unreadable marker"}
    return {"paused": marker.exists(), "marker": str(marker), "payload": payload}


def run_once(
    db_path: str | None = None,
    channel: str = DEFAULT_NOTIFICATION_CHANNEL,
    worker_id: str = "tmh-worker",
    dispatch_limit: int = 10,
) -> dict:
    ensure_db(db_path)
    status = worker_status(db_path)
    if status["paused"]:
        return {"paused": True, "worker": status, "enqueued": [], "dispatched": []}
    enqueued = enqueue_due_notifications(db_path=db_path, channel=channel)
    claimed = claim_ready_notification_jobs(worker_id=worker_id, db_path=db_path, limit=dispatch_limit)
    dispatched = []
    for job in claimed:
        task = job.get("task") or {}
        if task.get("status") in {"completed", "cancelled", "archived", "notified", "acknowledged", "in_progress"}:
            updated = record_notification_attempt(
                job["job_id"],
                "skipped",
                db_path=db_path,
                error=f"task status is {task.get('status')}",
            )
            dispatched.append({"job_id": job["job_id"], "status": updated["status"], "skipped": True})
            continue
        try:
            response = dispatch_notification(job)
            updated = record_notification_attempt(job["job_id"], "sent", db_path=db_path, response=response)
            dispatched.append({"job_id": job["job_id"], "status": updated["status"], "response": response})
        except NotificationDispatchError as exc:
            updated = record_notification_attempt(
                job["job_id"],
                "retry",
                db_path=db_path,
                error=str(exc),
                retry_delay_seconds=60,
            )
            dispatched.append({"job_id": job["job_id"], "status": updated["status"], "error": str(exc)})
    return {"enqueued": enqueued, "dispatched": dispatched}


def run_loop(
    db_path: str | None = None,
    channel: str = DEFAULT_NOTIFICATION_CHANNEL,
    interval: int = 60,
    worker_id: str = "tmh-worker",
    dispatch_limit: int = 10,
) -> None:
    ensure_db(db_path)
    print(f"{APP_NAME} worker started. channel={channel} interval={interval}s")
    while True:
        result = run_once(
            db_path=db_path,
            channel=channel,
            worker_id=worker_id,
            dispatch_limit=dispatch_limit,
        )
        if result["enqueued"] or result["dispatched"]:
            print(json.dumps(result, ensure_ascii=False))
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Run {APP_NAME} worker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            f"  tmh-worker --channel {DEFAULT_NOTIFICATION_CHANNEL} --interval 60\n"
            f"  tmh-worker --once --channel {DEFAULT_NOTIFICATION_CHANNEL}\n"
            "  tmh-worker --pause --reason manual\n"
            "  tmh-worker --resume\n"
        ),
    )
    parser.add_argument("--db")
    parser.add_argument("--channel", default=DEFAULT_NOTIFICATION_CHANNEL)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--worker-id", default="tmh-worker")
    parser.add_argument("--dispatch-limit", type=int, default=10)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--pause", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args(argv)

    if args.pause:
        print(json.dumps(pause_worker(db_path=args.db, reason=args.reason), ensure_ascii=False, indent=2))
        return 0

    if args.resume:
        print(json.dumps(resume_worker(db_path=args.db), ensure_ascii=False, indent=2))
        return 0

    if args.status:
        print(json.dumps(worker_status(db_path=args.db), ensure_ascii=False, indent=2))
        return 0

    if args.once:
        result = run_once(
            db_path=args.db,
            channel=args.channel,
            worker_id=args.worker_id,
            dispatch_limit=args.dispatch_limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    run_loop(
        db_path=args.db,
        channel=args.channel,
        interval=args.interval,
        worker_id=args.worker_id,
        dispatch_limit=args.dispatch_limit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
