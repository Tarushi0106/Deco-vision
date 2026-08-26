"""CLI wrapper for end-of-day unique-footfall report generation. The backend
now also runs this automatically via APScheduler (see app/scheduler.py) as
long as the process stays up — this script remains for a manual run, an
external cron / Windows Task Scheduler entry as a belt-and-suspenders
alternative, or backfilling a specific past date:

    python scripts/generate_footfall_report.py
    python scripts/generate_footfall_report.py --date 2026-08-23

Both paths call the exact same app.footfall_report.generate_and_save_report()
used by the in-process scheduler, so a manual run produces byte-identical
output to the automatic one.
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import footfall_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the daily unique-footfall report.")
    parser.add_argument(
        "--date", default=None,
        help="YYYY-MM-DD to report on (default: yesterday, for an after-midnight cron run)",
    )
    args = parser.parse_args()
    date = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    report = footfall_report.generate_and_save_report(date)
    print(
        f"Footfall report for {date}: {report['total']} unique visitor(s) across "
        f"{len(report['by_camera'])} camera(s).\n"
        f"  CSV:  {report['csv_path']}\n"
        f"  XLSX: {report['xlsx_path']}"
    )


if __name__ == "__main__":
    main()
