"""In-process end-of-day footfall report job (APScheduler), as an
alternative to running scripts/generate_footfall_report.py via an external
cron/Task Scheduler entry — this way the report finalizes itself as long as
the backend process is running, with no OS-level scheduler to configure.

Started/stopped from main.py's startup/shutdown events, right alongside
pipeline_manager. Uses BackgroundScheduler (its own thread) rather than
AsyncIOScheduler: the job body does blocking file I/O (writing CSV/XLSX),
which would otherwise stall the FastAPI event loop for its duration.
"""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config, footfall_report

logger = logging.getLogger("dashboard.footfall.scheduler")

_scheduler: BackgroundScheduler | None = None

# How late the scheduler is willing to run a job whose fire time has already
# passed (e.g. the backend was down over midnight) — long enough to cover a
# typical overnight outage, without piling up ancient missed runs forever.
MISFIRE_GRACE_SECONDS = 6 * 3600


def _finalize_yesterday() -> None:
    date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        report = footfall_report.generate_and_save_report(date)
        logger.info(
            "Footfall: finalized %s — %d unique visitor(s) across %d camera(s) (%s, %s)",
            date, report["total"], len(report["by_camera"]), report["csv_path"], report["xlsx_path"],
        )
    except Exception:
        logger.exception("Footfall: end-of-day report generation failed for %s", date)


def _catch_up_if_missed() -> None:
    """Runs once at startup: if the backend was down through yesterday's
    scheduled finalize time (so the cron trigger never got a chance to fire
    for it — misfire_grace_time only covers the scheduler being alive but
    late, not the whole process being offline), generate it now instead of
    silently leaving that day unfinalized until someone notices."""
    date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if (footfall_report.REPORTS_DIR / f"footfall_{date}.csv").exists():
        return
    logger.info("Footfall: no saved report found for %s yet — generating it now", date)
    _finalize_yesterday()


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return

    _catch_up_if_missed()

    hour, minute = (int(p) for p in config.FOOTFALL_REPORT_FINALIZE_TIME.split(":"))
    _scheduler = BackgroundScheduler(timezone=datetime.now().astimezone().tzinfo)
    _scheduler.add_job(
        _finalize_yesterday,
        trigger=CronTrigger(hour=hour, minute=minute),
        id="footfall_finalize_daily",
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
    )
    _scheduler.start()
    logger.info("Footfall: end-of-day report job scheduled daily at %02d:%02d", hour, minute)


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
