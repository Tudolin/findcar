"""In-process APScheduler. Keep uvicorn at a single worker so jobs don't run twice."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.core.db import session_scope
from app.services import alerts, runner
from app.services.config_store import get_setting

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _summary_job() -> None:
    with session_scope() as s:
        alerts.send_daily_summary(s)


def configure(sched: BackgroundScheduler) -> None:
    tz = get_settings().timezone
    with session_scope() as s:
        times = get_setting(s, "schedule")["times"]
        acfg = get_setting(s, "alerts")
    for job in sched.get_jobs():
        job.remove()
    for t in times:
        hh, mm = (int(x) for x in t.split(":"))
        sched.add_job(runner.run_all, CronTrigger(hour=hh, minute=mm, timezone=tz),
                      id=f"run-{t}", max_instances=1, coalesce=True, misfire_grace_time=1800)
    if acfg.get("daily_summary"):
        hh, mm = (int(x) for x in acfg.get("daily_summary_time", "20:00").split(":"))
        sched.add_job(_summary_job, CronTrigger(hour=hh, minute=mm, timezone=tz),
                      id="daily-summary", coalesce=True)
    log.info("scheduler configured", extra={"times": times})


def start() -> None:
    global _scheduler
    if _scheduler or not get_settings().scheduler_enabled:
        return
    _scheduler = BackgroundScheduler(timezone=get_settings().timezone)
    configure(_scheduler)
    _scheduler.start()


def reload() -> None:
    if _scheduler:
        configure(_scheduler)


def next_runs() -> list:
    if not _scheduler:
        return []
    return sorted(j.next_run_time for j in _scheduler.get_jobs() if j.id.startswith("run-")
                   and j.next_run_time)


def shutdown() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
