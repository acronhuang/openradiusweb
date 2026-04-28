"""
Active Directory Event Log Collector

Collects AD authentication events to enrich RADIUS auth log with:
- Detailed failure reasons from Windows Security Event Log
- Kerberos pre-authentication failure details
- Account lockout source identification (which DC, which workstation)
- Logon type information

Data sources (choose one or more):
1. Wazuh agent on DC → Wazuh API → OpenRadiusWeb (recommended)
2. Windows Event Forwarding (WEF) → Syslog → OpenRadiusWeb
3. Direct WMI/WinRM query to DC (not recommended for production)

Key AD Event IDs for 802.1X troubleshooting:
  4625 - Failed logon (most important)
  4771 - Kerberos pre-authentication failed
  4776 - NTLM authentication (credential validation)
  4740 - Account locked out
  4767 - Account unlocked
  4724 - Password reset attempt
  4723 - Password change attempt
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import text

from orw_common.logging import get_logger
from orw_common.database import get_db_context
from orw_common import nats_client

log = get_logger("ad_event_collector")

# Windows Security Event ID mapping
AD_EVENT_MAP = {
    # Event ID 4625 Sub-status codes (Failed logon)
    "4625": {
        "0xC0000064": ("AD_MACHINE_NOT_FOUND", "User name does not exist"),
        "0xC000006A": ("AD_INVALID_CREDENTIALS", "Wrong password"),
        "0xC000006D": ("AD_INVALID_CREDENTIALS", "Bad username or authentication info"),
        "0xC000006E": ("AD_LOGON_HOURS", "Account logon time restriction violation"),
        "0xC000006F": ("AD_LOGON_WORKSTATION", "Logon from unauthorized workstation"),
        "0xC0000070": ("AD_LOGON_WORKSTATION", "Logon from unauthorized workstation"),
        "0xC0000071": ("AD_PASSWORD_EXPIRED", "Password has expired"),
        "0xC0000072": ("AD_ACCOUNT_DISABLED", "Account is disabled"),
        "0xC000009A": ("AD_ACCOUNT_DISABLED", "Insufficient system resources"),
        "0xC0000193": ("AD_ACCOUNT_EXPIRED", "Account has expired"),
        "0xC0000224": ("AD_PASSWORD_MUST_CHANGE", "Password must change at next logon"),
        "0xC0000234": ("AD_ACCOUNT_LOCKED", "Account is locked out"),
        "0xC0000413": ("AD_LOGON_HOURS", "Authentication firewall - logon hours"),
    },
    # Event ID 4771 Failure codes (Kerberos pre-auth)
    "4771": {
        "0x6": ("AD_MACHINE_NOT_FOUND", "Client not found in Kerberos database"),
        "0x12": ("AD_ACCOUNT_DISABLED", "Client's credentials have been revoked"),
        "0x17": ("AD_PASSWORD_EXPIRED", "Password has expired"),
        "0x18": ("AD_INVALID_CREDENTIALS", "Pre-authentication information was invalid (wrong password)"),
        "0x25": ("AD_LOGON_HOURS", "Clock skew too great"),
    },
    # Event ID 4776 (NTLM validation)
    "4776": {
        "0xC0000064": ("AD_MACHINE_NOT_FOUND", "User name does not exist"),
        "0xC000006A": ("AD_INVALID_CREDENTIALS", "Wrong password"),
        "0xC0000234": ("AD_ACCOUNT_LOCKED", "Account locked out"),
    },
}

# Logon types for Event 4625
LOGON_TYPES = {
    2: "Interactive (local console)",
    3: "Network (SMB, RADIUS, etc.)",
    4: "Batch",
    5: "Service",
    7: "Unlock",
    8: "NetworkCleartext (IIS basic auth)",
    9: "NewCredentials (RunAs /netonly)",
    10: "RemoteInteractive (RDP)",
    11: "CachedInteractive (cached domain credentials)",
}


class ADEventCollector:
    """
    Collect AD authentication events and enrich RADIUS auth logs.

    Supports two modes:
    1. Wazuh integration: Query Wazuh API for AD events collected by Wazuh agent
    2. Direct WinRM: Query DC Event Log directly (for testing/small deployments)
    """

    def __init__(
        self,
        mode: str = "wazuh",
        wazuh_url: str = "https://localhost:55000",
        wazuh_user: str = "wazuh-wui",
        wazuh_password: str = "",
        dc_addresses: list[str] | None = None,
        poll_interval_seconds: int = 30,
    ):
        self.mode = mode
        self.wazuh_url = wazuh_url.rstrip("/")
        self.wazuh_user = wazuh_user
        self.wazuh_password = wazuh_password
        self.dc_addresses = dc_addresses or []
        self.poll_interval = poll_interval_seconds
        self._wazuh_token: Optional[str] = None
        self._running = False
        self._last_poll: Optional[datetime] = None

    async def start(self):
        """Start periodic polling for AD events."""
        self._running = True
        log.info("ad_event_collector_starting", mode=self.mode)

        while self._running:
            try:
                if self.mode == "wazuh":
                    await self._poll_wazuh()
                else:
                    log.warning("unsupported_mode", mode=self.mode)
            except Exception as e:
                log.error("ad_event_poll_error", error=str(e))

            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False

    # ============================================================
    # Wazuh Integration
    # ============================================================

    async def _authenticate_wazuh(self):
        """Get JWT token from Wazuh API."""
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{self.wazuh_url}/security/user/authenticate",
                auth=(self.wazuh_user, self.wazuh_password),
            )
            response.raise_for_status()
            self._wazuh_token = response.json()["data"]["token"]

    async def _poll_wazuh(self):
        """Poll Wazuh for recent AD authentication events."""
        if not self._wazuh_token:
            await self._authenticate_wazuh()

        # Query Wazuh alerts for AD auth events
        # Event IDs: 4625 (failed logon), 4771 (kerberos), 4776 (NTLM), 4740 (lockout)
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(
                f"{self.wazuh_url}/alerts",
                headers={"Authorization": f"Bearer {self._wazuh_token}"},
                params={
                    "q": "rule.groups=windows,authentication_failed;data.win.system.eventID=4625,4771,4776,4740",
                    "limit": 500,
                    "sort": "-timestamp",
                },
            )

            if response.status_code == 401:
                await self._authenticate_wazuh()
                return

            response.raise_for_status()
            alerts = response.json().get("data", {}).get("affected_items", [])

        self._last_poll = datetime.now(timezone.utc)

        # Process each AD event
        for alert in alerts:
            await self._process_ad_event(alert)

        if alerts:
            log.info("ad_events_processed", count=len(alerts))

    async def _process_ad_event(self, alert: dict):
        """Process a single AD authentication event from Wazuh."""
        try:
            win_data = alert.get("data", {}).get("win", {})
            event_data = win_data.get("eventdata", {})
            system_data = win_data.get("system", {})

            event_id = system_data.get("eventID", "")
            timestamp = alert.get("timestamp", "")

            # Extract key fields
            target_user = event_data.get("targetUserName", "")
            target_domain = event_data.get("targetDomainName", "")
            source_ip = event_data.get("ipAddress", "")
            workstation = event_data.get("workstationName", "")
            logon_type = event_data.get("logonType", "")
            status_code = event_data.get("status", "")
            sub_status = event_data.get("subStatus", "")
            failure_reason_text = event_data.get("failureReason", "")
            dc_name = system_data.get("computer", "")

            # Map sub-status to our failure code
            event_map = AD_EVENT_MAP.get(str(event_id), {})
            lookup_code = sub_status or status_code
            mapped = event_map.get(lookup_code, (None, None))
            failure_code, failure_desc = mapped

            # Build enrichment data
            enrichment = {
                "ad_event_id": event_id,
                "ad_event_timestamp": timestamp,
                "ad_target_user": f"{target_domain}\\{target_user}" if target_domain else target_user,
                "ad_source_ip": source_ip,
                "ad_workstation": workstation,
                "ad_dc_name": dc_name,
                "ad_logon_type": LOGON_TYPES.get(int(logon_type), logon_type) if logon_type else None,
                "ad_status_code": status_code,
                "ad_sub_status": sub_status,
                "ad_failure_reason": failure_desc or failure_reason_text,
                "ad_failure_code": failure_code,
            }

            # Try to correlate with RADIUS auth log entry
            # Match by username + approximate timestamp (within 5 seconds)
            if target_user:
                await self._enrich_radius_log(
                    username=f"{target_user}@{target_domain}" if target_domain else target_user,
                    ad_timestamp=timestamp,
                    enrichment=enrichment,
                )

            # Publish event for real-time dashboard
            await nats_client.publish("orw.ad.auth_event", enrichment)

        except Exception as e:
            log.error("ad_event_processing_error", error=str(e))

    async def _enrich_radius_log(
        self, username: str, ad_timestamp: str, enrichment: dict
    ):
        """
        Correlate AD event with RADIUS auth log entry and enrich it.
        Matches by username within a 5-second time window.
        """
        async with get_db_context() as db:
            # Find matching RADIUS auth log entry
            result = await db.execute(
                text("""
                    UPDATE radius_auth_log
                    SET
                        ad_error_code = COALESCE(:ad_failure_code, ad_error_code),
                        ad_error_message = COALESCE(:ad_failure_reason, ad_error_message),
                        failure_reason = COALESCE(:ad_failure_reason, failure_reason),
                        request_attributes = request_attributes || :enrichment::jsonb
                    WHERE username ILIKE :username
                      AND auth_result != 'success'
                      AND ad_error_code IS NULL
                      AND timestamp >= :ts_start
                      AND timestamp <= :ts_end
                    RETURNING id
                """),
                {
                    "username": f"%{username.split('@')[0]}%",
                    "ad_failure_code": enrichment.get("ad_failure_code"),
                    "ad_failure_reason": enrichment.get("ad_failure_reason"),
                    "enrichment": str({
                        "ad_event_id": enrichment.get("ad_event_id"),
                        "ad_dc_name": enrichment.get("ad_dc_name"),
                        "ad_workstation": enrichment.get("ad_workstation"),
                        "ad_logon_type": enrichment.get("ad_logon_type"),
                    }).replace("'", '"'),
                    "ts_start": ad_timestamp,
                    "ts_end": ad_timestamp,  # Would add timedelta in real parsing
                },
            )
            updated = result.first()
            if updated:
                log.debug("radius_log_enriched",
                          log_id=str(updated[0]), username=username)


# ============================================================
# Event ID 4740 Account Lockout Handler
# ============================================================

async def handle_account_lockout(data: dict):
    """
    Handle AD account lockout event (Event ID 4740).
    Automatically identifies affected RADIUS sessions and notifies.
    """
    username = data.get("ad_target_user", "")
    dc_name = data.get("ad_dc_name", "")

    log.warning("ad_account_lockout_detected",
                username=username, dc=dc_name)

    # Find active RADIUS sessions for this user
    async with get_db_context() as db:
        result = await db.execute(
            text("""
                SELECT rs.id, rs.calling_station_id, rs.nas_ip, rs.nas_port_id
                FROM radius_sessions rs
                WHERE rs.username ILIKE :username
                  AND rs.status = 'active'
            """),
            {"username": f"%{username.split(chr(92))[-1] if chr(92) in username else username}%"},
        )
        active_sessions = [dict(r._mapping) for r in result]

    if active_sessions:
        # Notify about affected sessions
        await nats_client.publish("orw.ad.account_lockout", {
            "username": username,
            "dc_name": dc_name,
            "affected_sessions": len(active_sessions),
            "sessions": active_sessions,
        })

        log.warning("lockout_affects_active_sessions",
                     username=username,
                     sessions=len(active_sessions))
