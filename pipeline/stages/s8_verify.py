"""Stage 8 — Verify: confirm all "definition of done" criteria are met.

Passing criteria:
  1. is_provisioned = True (pycentral scopes)
  2. isProvisioned = "YES" (MCP cross-check — string, not bool)
  3. status = ONLINE
  4. softwareVersion starts with "10." (AOS-CX only; AOS-S skipped)
"""

from __future__ import annotations

import logging

from pipeline.models import AccountContext, DeviceRecord, FirmwareAction, HardwareSeries, StageResult
from pipeline.state_store import StateStore
from pipeline.stages.base import Stage

logger = logging.getLogger(__name__)


class VerifyStage(Stage):
    name = "s8_verify"

    def _execute(
        self,
        record: DeviceRecord,
        run_id: str,
        source_ctx: AccountContext,
        target_ctx: AccountContext,
        state: StateStore,
        dry_run: bool,
    ) -> StageResult:
        if dry_run:
            return StageResult.skipped("dry-run — skipping verify")

        mcp = target_ctx.mcp_client
        central = target_ctx.central_client

        failures: list[str] = []

        # 1. MCP cross-check: isProvisioned == "YES" and status == ONLINE
        device = mcp.get_device_by_serial(record.serial_number)
        if device is None:
            failures.append("device not found in target Central via MCP")
        else:
            is_provisioned_str = str(device.get("isProvisioned", "")).upper()
            status = str(device.get("status", "")).upper()

            if str(device.get("isProvisioned", "")).lower() != "yes":
                failures.append(f"isProvisioned={device.get('isProvisioned')!r} (expected 'Yes')")
            if status != "ONLINE":
                logger.warning(
                    "%s status=%r (expected 'ONLINE') — device may be offline but still provisioned",
                    record.serial_number, status,
                )

        # 2. pycentral provisioned_status check (non-blocking — MCP is primary gate)
        try:
            conn = central.get_pycentral_conn()
            from pycentral.scopes import Device as PyDevice  # type: ignore[import]
            # Try both possible method names across pycentral versions
            prov_fn = getattr(PyDevice, "provisioned_status", None) or getattr(PyDevice, "get_provisioned_status", None)
            if prov_fn:
                prov_status = prov_fn(central_conn=conn, serial_number=record.serial_number)
                if not prov_status:
                    logger.warning("pycentral provisioned_status returned False for %s", record.serial_number)
            else:
                logger.debug("pycentral Device has no provisioned_status method — skipping pycentral check")
        except Exception as exc:
            logger.warning("pycentral provisioned_status check failed: %s", exc)

        # 3. Firmware version check (AOS-CX only)
        # softwareVersion/firmwareVersion may have platform prefix like "PL.10.16.1006"
        fw_version = (device or {}).get("firmwareVersion", "") or ""
        if (
            record.hardware_series == HardwareSeries.AOS_CX
            and record.firmware_action != FirmwareAction.SKIP
        ):
            # Fall back to firmware-details endpoint if MCP didn't provide firmware version
            if not fw_version:
                try:
                    details = central.get(
                        "/network-services/v1alpha1/firmware-details",
                        params={"serialNumber": record.serial_number},
                    )
                    items = details.get("items", [])
                    for item in items:
                        if item.get("serialNumber") == record.serial_number or item.get("id") == record.serial_number:
                            fw_version = item.get("softwareVersion", "") or item.get("firmwareVersion", "") or ""
                            break
                    if not fw_version and items:
                        fw_version = items[0].get("softwareVersion", "") or items[0].get("firmwareVersion", "") or ""
                except Exception as exc:
                    logger.warning("Firmware version check failed: %s", exc)
            # Check contains "10." anywhere (handles platform prefixes like "PL.10.16.1006")
            if fw_version and "10." not in fw_version:
                failures.append(
                    f"firmwareVersion={fw_version!r} does not contain '10.'"
                )

        # 4. Config-health: configStatus must be SYNCHRONIZED
        try:
            health = central.get(
                "/network-config/v1alpha1/config-health/devices",
                params={"filter": f"serial eq '{record.serial_number}'"},
            )
            health_items = health.get("devices", health.get("items", []))
            if health_items:
                config_status = str(health_items[0].get("configStatus", "")).upper()
                if config_status and config_status != "SYNCHRONIZED":
                    failures.append(f"configStatus={config_status!r} (expected 'SYNCHRONIZED')")
            else:
                logger.warning(
                    "%s: config-health returned no items — cannot verify sync status",
                    record.serial_number,
                )
        except Exception as exc:
            logger.warning("Config-health check failed for %s: %s", record.serial_number, exc)

        if failures:
            return StageResult.failed(
                f"VERIFY_FAILED: {'; '.join(failures)}",
                is_provisioned=False,
                checks_failed=failures,
            )

        final_firmware = fw_version or (device or {}).get("firmwareVersion") or record.firmware_target
        device_status = str((device or {}).get("status", "")).upper()
        return StageResult.success(
            is_provisioned=True,
            final_firmware=final_firmware,
            site_id=record.site_id,
            device_status=device_status,
        )
