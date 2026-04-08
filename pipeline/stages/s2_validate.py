"""Stage 2 — Validate: pre-flight checks via read-only MCP tools."""

from __future__ import annotations

import logging
from typing import Any

from pipeline.models import AccountContext, DeviceRecord, FirmwareAction, HardwareSeries, StageResult
from pipeline.state_store import StateStore
from pipeline.stages.base import Stage

logger = logging.getLogger(__name__)


def _get_group_names(central_client: Any) -> set[str]:
    """Fetch device group names. Tries /v1/ first, falls back to /v1alpha1/."""
    for path in ("/network-config/v1/device-groups", "/network-config/v1alpha1/device-groups"):
        try:
            result = central_client.get(path, params={"limit": 100})
            items = result.get("items", result.get("data", []))
            if items is not None:
                return {g.get("scopeName", g.get("group", g.get("name", ""))) for g in items}
        except Exception:
            continue
    return set()


class ValidateStage(Stage):
    name = "s2_validate"

    def _execute(
        self,
        record: DeviceRecord,
        run_id: str,
        source_ctx: AccountContext,
        target_ctx: AccountContext,
        state: StateStore,
        dry_run: bool,
    ) -> StageResult:
        mcp = target_ctx.mcp_client

        warnings: list[str] = []

        # 1. Confirm device is not already provisioned in target
        existing = mcp.get_device_by_serial(record.serial_number)
        if existing and str(existing.get("isProvisioned", "")).lower() == "yes":
            return StageResult.failed(
                f"VALIDATION_FAILED: device {record.serial_number} is already provisioned "
                "in the target account — skipping to avoid duplicate migration."
            )

        # 2. Check if target_site exists
        site = mcp.get_site_by_name(record.target_site)
        if site:
            record.site_id = site.get("id") or site.get("siteId") or site.get("site_id")
            record.needs_site_create = False
            logger.debug("Site '%s' exists → site_id=%s", record.target_site, record.site_id)
        else:
            record.needs_site_create = True
            logger.info("Site '%s' does not exist — will create in S6", record.target_site)

        # 3. Check if target_group exists (blocking)
        group_names = _get_group_names(target_ctx.central_client)
        if record.target_group not in group_names:
            return StageResult.failed(
                f"VALIDATION_FAILED: target_group '{record.target_group}' does not exist "
                "in the target Central account. Create it before running the pipeline."
            )

        # 4. Check for active critical alerts (warn only)
        if record.site_id:
            alerts = mcp.get_alerts(site_id=record.site_id, severity="Critical")
            if alerts:
                warnings.append(
                    f"{len(alerts)} active critical alert(s) on site '{record.target_site}'"
                )
                logger.warning(
                    "Serial %s: %d critical alert(s) on target site — proceeding anyway",
                    record.serial_number,
                    len(alerts),
                )

        # 5. Config-health pre-check (warn only — device may already be in target account)
        if existing:
            try:
                health = target_ctx.central_client.get(
                    "/network-config/v1alpha1/config-health/devices",
                    params={"filter": f"serial eq '{record.serial_number}'"},
                )
                health_items = health.get("devices", health.get("items", []))
                if health_items:
                    config_status = str(health_items[0].get("configStatus", "")).upper()
                    if config_status and config_status != "SYNCHRONIZED":
                        warnings.append(
                            f"configStatus={config_status!r} in target (device may need reconfiguration)"
                        )
            except Exception as exc:
                logger.warning("Config-health preflight check failed for %s: %s", record.serial_number, exc)

        # 7. AOS-S + AOS 10 target → mark firmware as skip
        if record.hardware_series == HardwareSeries.AOS_S and record.firmware_target.startswith("10."):
            record.firmware_action = FirmwareAction.SKIP
            warnings.append("AOS-S hardware cannot run AOS 10 — firmware upgrade will be skipped")

        return StageResult.success(
            site_id=record.site_id,
            needs_site_create=record.needs_site_create,
            firmware_action=record.firmware_action.value,
            warnings=warnings,
        )
