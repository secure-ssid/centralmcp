"""Write per-device migration results to a CSV report."""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from pipeline.models import DeviceRecord, OverallStatus, StageStatus
from pipeline.state_store import StateStore

logger = logging.getLogger(__name__)

STAGES = ["s1_discover", "s2_validate", "s3_offboard", "s4_transfer",
          "s5_onboard", "s6_configure", "s7_firmware", "s8_verify"]

COLUMNS = [
    "serial_number",
    "source_type",
    "target_account",
    "overall_status",
    *[f"s{i+1}" for i in range(len(STAGES))],
    "is_provisioned",
    "final_firmware",
    "site_id",
    "error_detail",
    "duration_seconds",
    "notes",
]


def _overall_status(stage_statuses: dict[str, str]) -> OverallStatus:
    statuses = set(stage_statuses.values())
    if StageStatus.FAILED.value in statuses:
        all_done = all(
            v in (StageStatus.SUCCESS.value, StageStatus.SKIPPED.value, StageStatus.FAILED.value)
            for v in stage_statuses.values()
        )
        # If s8 succeeded despite earlier failures it can't happen, but check s8
        if stage_statuses.get("s8_verify") == StageStatus.SUCCESS.value:
            return OverallStatus.DONE
        return OverallStatus.FAILED
    if all(v == StageStatus.SKIPPED.value for v in stage_statuses.values()):
        return OverallStatus.SKIPPED
    if stage_statuses.get("s8_verify") == StageStatus.SUCCESS.value:
        return OverallStatus.DONE
    if any(v == StageStatus.SUCCESS.value for v in stage_statuses.values()):
        return OverallStatus.PARTIAL
    # No failures, not all skipped, no successes yet → still in progress.
    return OverallStatus.PARTIAL


def _stage_error_message(state: StateStore, serial: str, run_id: str, stage: str) -> str:
    """Read the error_message column for a stage (StateStore has no public getter)."""
    with state._conn() as conn:
        row = conn.execute(
            "SELECT error_message FROM device_state WHERE serial_number=? AND run_id=? AND stage=?",
            (serial, run_id, stage),
        ).fetchone()
    if row and row["error_message"]:
        return str(row["error_message"])
    return ""


def write_report(
    records: list[DeviceRecord],
    run_id: str,
    state: StateStore,
    output_dir: str = "outputs",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> str:
    """Write a CSV report and return the output file path."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = output_path / f"migration_report_{run_id}_{ts}.csv"

    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()

        for record in records:
            stage_statuses = state.get_all_stage_statuses(record.serial_number, run_id)
            overall = _overall_status(stage_statuses)

            # Pull verify data for final fields
            verify_data = state.get_stage_data(record.serial_number, run_id, "s8_verify")

            # Find first failure message. The error string lives in the
            # device_state.error_message column (StageResult.failed(error=...)),
            # not in result_data. Some stages also stash a list of failed
            # checks in result_data (e.g. s8_verify "checks_failed") — fall
            # back to that if the column is empty.
            error_detail = ""
            for stage in STAGES:
                if stage_statuses.get(stage) == StageStatus.FAILED.value:
                    error_detail = _stage_error_message(state, record.serial_number, run_id, stage) or ""
                    if not error_detail:
                        stage_data = state.get_stage_data(record.serial_number, run_id, stage)
                        checks = stage_data.get("checks_failed") or stage_data.get("errors")
                        if isinstance(checks, list):
                            error_detail = "; ".join(str(c) for c in checks)
                    break

            row: dict = {
                "serial_number": record.serial_number,
                "source_type": record.source_type.value,
                "target_account": record.target_account.value,
                "overall_status": overall.value,
                "is_provisioned": verify_data.get("is_provisioned", ""),
                "final_firmware": verify_data.get("final_firmware", ""),
                "site_id": verify_data.get("site_id", record.site_id or ""),
                "error_detail": error_detail,
                "duration_seconds": "",
                "notes": record.notes or "",
            }

            for i, stage in enumerate(STAGES):
                row[f"s{i+1}"] = stage_statuses.get(stage, StageStatus.PENDING.value)

            writer.writerow(row)

    logger.info("Report written to %s", filename)
    return str(filename)
