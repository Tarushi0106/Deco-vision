"""Camera-name enrichment + on-demand/scheduled report generation for the
unique-footfall feature (see footfall_counter.py / footfall_db.py for the
counting/storage side). Shared by:
  - main.py's GET /api/footfall/report* endpoints (enrichment only — those
    build their response in memory, they don't write files)
  - scheduler.py's end-of-day APScheduler job (finalizes + saves to disk)
  - scripts/generate_footfall_report.py, a thin CLI wrapper around the same
    generate_and_save_report() so a manual/cron run produces byte-identical
    output to the scheduled job.
"""

import csv
from pathlib import Path

from . import camera_db, footfall_db

REPORTS_DIR = Path(__file__).resolve().parent.parent / "data" / "footfall_reports"


def enrich_with_camera_names(report: dict) -> dict:
    """footfall_db.get_daily_report() only knows camera_id — this attaches
    the human-readable camera_name to each by_camera/visits entry, the same
    way every other report in this app (attendance, analytics) does it."""
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    for cam in report["by_camera"]:
        cam["camera_name"] = cameras_by_id.get(cam["camera_id"], "—")
    for visit in report["visits"]:
        visit["camera_name"] = cameras_by_id.get(visit["camera_id"], "—")
    return report


def generate_and_save_report(date: str | None = None) -> dict:
    """On-demand report generation for one day (default: today) — the
    "finalize the day" step. Writes a CSV + XLSX snapshot to
    backend/data/footfall_reports/ and returns the report dict (with
    csv_path/xlsx_path added) — safe to call from the scheduler, the CLI
    script, or directly from other backend code."""
    report = enrich_with_camera_names(footfall_db.get_daily_report(date))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = REPORTS_DIR / f"footfall_{report['date']}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        footfall_db.write_csv_rows(report, csv.writer(f))

    xlsx_path = REPORTS_DIR / f"footfall_{report['date']}.xlsx"
    footfall_db.build_workbook(report).save(xlsx_path)

    report["csv_path"] = str(csv_path)
    report["xlsx_path"] = str(xlsx_path)
    return report
