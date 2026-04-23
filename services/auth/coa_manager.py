"""
RADIUS Change of Authorization (CoA) Manager - RFC 5176 / RFC 3576

CoA allows OpenRadiusWeb to dynamically change a user's network access AFTER
initial authentication, without requiring the user to disconnect/reconnect.

Use cases:
  1. Policy change → re-evaluate → CoA to move device to new VLAN
  2. Compliance failure detected → CoA to quarantine VLAN
  3. Threat detected (SIEM alert) → CoA disconnect
  4. Account locked in AD → CoA disconnect active sessions
  5. Guest registration completed → CoA to upgrade from Guest to Registered VLAN
  6. Time-based policy → CoA when business hours end

CoA Message Types (UDP port 3799):
  - CoA-Request (Code 43): Change session attributes (VLAN, ACL, etc.)
  - Disconnect-Request (Code 40): Terminate a session
  - CoA-ACK (Code 44): Success response from NAS
  - CoA-NAK (Code 45): Failure response from NAS
  - Disconnect-ACK (Code 41): Success response
  - Disconnect-NAK (Code 42): Failure response

Session identification (at least one required in CoA packet):
  - Acct-Session-Id: RADIUS session ID
  - User-Name: Username
  - Calling-Station-Id: MAC address
  - NAS-Port: Physical port number

How switches must be configured:
  Cisco IOS:
    aaa server radius dynamic-author
      client 10.0.0.5 server-key <shared-secret>
      auth-type any

  Aruba/HPE:
    radius dynamic-authorization
      enable
      client 10.0.0.5 key <shared-secret>

  Juniper Junos:
    access profile radius-profile {
        dynamic-authorization;
    }
"""

import asyncio
import hashlib
import hmac
import os
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Optional

from orw_common.logging import get_logger
from orw_common.database import get_db_context
from orw_common import nats_client

log = get_logger("coa_manager")


# ============================================================
# RADIUS Packet Constants (RFC 2865, RFC 5176)
# ============================================================

class RadiusCode(IntEnum):
    """RADIUS packet type codes."""
    ACCESS_REQUEST = 1
    ACCESS_ACCEPT = 2
    ACCESS_REJECT = 3
    ACCOUNTING_REQUEST = 4
    ACCOUNTING_RESPONSE = 5
    DISCONNECT_REQUEST = 40
    DISCONNECT_ACK = 41
    DISCONNECT_NAK = 42
    COA_REQUEST = 43
    COA_ACK = 44
    COA_NAK = 45


class RadiusAttr(IntEnum):
    """Common RADIUS attribute type codes."""
    USER_NAME = 1
    NAS_IP_ADDRESS = 4
    NAS_PORT = 5
    REPLY_MESSAGE = 18
    STATE = 24
    CALLING_STATION_ID = 31
    NAS_IDENTIFIER = 32
    ACCT_SESSION_ID = 44
    NAS_PORT_TYPE = 61
    NAS_PORT_ID = 87
    ERROR_CAUSE = 101

    # Vendor-specific (Cisco)
    VENDOR_SPECIFIC = 26

    # Tunnel attributes for VLAN reassignment via CoA
    TUNNEL_TYPE = 64
    TUNNEL_MEDIUM_TYPE = 65
    TUNNEL_PRIVATE_GROUP_ID = 81

    # Filter (ACL) attribute
    FILTER_ID = 11


# Cisco vendor-specific attribute IDs
CISCO_VENDOR_ID = 9
CISCO_AV_PAIR = 1  # Cisco-AVPair sub-attribute


# Error cause codes (RFC 5176 Section 3.2)
ERROR_CAUSE = {
    201: "Residual Session Context Removed",
    202: "Invalid EAP Packet",
    401: "Unsupported Attribute",
    402: "Missing Attribute",
    403: "NAS Identification Mismatch",
    404: "Invalid Request",
    405: "Unsupported Service",
    406: "Unsupported Extension",
    501: "Administratively Prohibited",
    502: "Request Not Routable (Proxy)",
    503: "Session Context Not Found",
    504: "Session Context Not Removable",
    505: "Other Proxy Processing Error",
    506: "Resources Unavailable",
    507: "Request Initiated",
    508: "Multiple Session Selection Unsupported",
}


# ============================================================
# RADIUS Packet Builder
# ============================================================

@dataclass
class RadiusPacket:
    """Build and parse RADIUS packets for CoA/Disconnect."""

    code: int
    identifier: int = 0
    authenticator: bytes = field(default_factory=lambda: os.urandom(16))
    attributes: list[tuple[int, bytes]] = field(default_factory=list)

    def add_string(self, attr_type: int, value: str):
        """Add a string attribute."""
        self.attributes.append((attr_type, value.encode("utf-8")))

    def add_integer(self, attr_type: int, value: int):
        """Add a 4-byte integer attribute."""
        self.attributes.append((attr_type, struct.pack("!I", value)))

    def add_ipv4(self, attr_type: int, ip: str):
        """Add an IPv4 address attribute."""
        parts = [int(p) for p in ip.split(".")]
        self.attributes.append((attr_type, struct.pack("BBBB", *parts)))

    def add_tunnel_vlan(self, vlan_id: int, tag: int = 0):
        """Add Tunnel-Type + Tunnel-Medium-Type + Tunnel-Private-Group-Id for VLAN change."""
        # Tunnel-Type = VLAN (13), tagged with tag byte
        self.attributes.append((
            RadiusAttr.TUNNEL_TYPE,
            struct.pack("!BI", tag, 13)[:4]  # tag(1) + value(3) = 4 bytes
        ))
        # Tunnel-Medium-Type = IEEE-802 (6)
        self.attributes.append((
            RadiusAttr.TUNNEL_MEDIUM_TYPE,
            struct.pack("!BI", tag, 6)[:4]
        ))
        # Tunnel-Private-Group-Id = VLAN ID as string
        vlan_bytes = struct.pack("B", tag) + str(vlan_id).encode("utf-8")
        self.attributes.append((RadiusAttr.TUNNEL_PRIVATE_GROUP_ID, vlan_bytes))

    def add_cisco_avpair(self, avpair: str):
        """Add Cisco-AVPair vendor-specific attribute (e.g., 'subscriber:command=reauthenticate')."""
        # VSA format: Vendor-Id(4) + Vendor-Type(1) + Vendor-Length(1) + Value
        avpair_bytes = avpair.encode("utf-8")
        vsa_data = struct.pack("!I", CISCO_VENDOR_ID)  # Cisco vendor ID
        vsa_data += struct.pack("BB", CISCO_AV_PAIR, len(avpair_bytes) + 2)
        vsa_data += avpair_bytes
        self.attributes.append((RadiusAttr.VENDOR_SPECIFIC, vsa_data))

    def encode(self, secret: str) -> bytes:
        """Encode the packet to bytes with proper authenticator."""
        # Build attributes section
        attrs_data = b""
        for attr_type, attr_value in self.attributes:
            attr_len = len(attr_value) + 2  # type(1) + length(1) + value
            attrs_data += struct.pack("BB", attr_type, attr_len) + attr_value

        # Total packet length
        length = 20 + len(attrs_data)  # header(20) + attributes

        # Build packet without authenticator first (for signing)
        header = struct.pack("!BBH", self.code, self.identifier, length)

        # For CoA/Disconnect Request: authenticator = MD5(Code+ID+Length+16-zero+Attrs+Secret)
        raw = header + (b"\x00" * 16) + attrs_data + secret.encode("utf-8")
        authenticator = hashlib.md5(raw).digest()

        return header + authenticator + attrs_data

    @classmethod
    def decode(cls, data: bytes, secret: str) -> "RadiusPacket":
        """Decode a RADIUS response packet."""
        if len(data) < 20:
            raise ValueError("Packet too short")

        code, identifier, length = struct.unpack("!BBH", data[:4])
        authenticator = data[4:20]
        attrs_data = data[20:length]

        # Parse attributes
        attributes = []
        pos = 0
        while pos < len(attrs_data):
            if pos + 2 > len(attrs_data):
                break
            attr_type = attrs_data[pos]
            attr_len = attrs_data[pos + 1]
            if attr_len < 2 or pos + attr_len > len(attrs_data):
                break
            attr_value = attrs_data[pos + 2:pos + attr_len]
            attributes.append((attr_type, attr_value))
            pos += attr_len

        pkt = cls(code=code, identifier=identifier, authenticator=authenticator)
        pkt.attributes = attributes
        return pkt

    def get_attr_string(self, attr_type: int) -> Optional[str]:
        """Get a string attribute value."""
        for t, v in self.attributes:
            if t == attr_type:
                return v.decode("utf-8", errors="replace")
        return None

    def get_error_cause(self) -> Optional[int]:
        """Get Error-Cause attribute value."""
        for t, v in self.attributes:
            if t == RadiusAttr.ERROR_CAUSE and len(v) >= 4:
                return struct.unpack("!I", v[:4])[0]
        return None


# ============================================================
# CoA Manager
# ============================================================

COA_PORT = 3799  # RFC 5176 standard CoA port
COA_TIMEOUT = 5  # seconds


@dataclass
class CoAResult:
    """Result of a CoA/Disconnect operation."""
    success: bool
    action: str
    nas_ip: str
    session_id: Optional[str] = None
    mac_address: Optional[str] = None
    response_code: Optional[int] = None
    error_cause: Optional[int] = None
    error_message: Optional[str] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "action": self.action,
            "nas_ip": self.nas_ip,
            "session_id": self.session_id,
            "mac_address": self.mac_address,
            "response_code": self.response_code,
            "error_cause": self.error_cause,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
        }


class CoAManager:
    """
    Send RADIUS CoA and Disconnect packets to network switches.

    Supports three main operations:
    1. reauthenticate - Force 802.1X re-authentication (applies new policies)
    2. disconnect - Terminate session (device must reconnect)
    3. vlan_change - Change VLAN without disconnecting (CoA with Tunnel attrs)
    """

    def __init__(self):
        self._identifier = 0  # Packet identifier counter

    def _next_id(self) -> int:
        """Get next packet identifier (0-255)."""
        self._identifier = (self._identifier + 1) % 256
        return self._identifier

    # ============================================================
    # Core CoA Operations
    # ============================================================

    async def disconnect_session(
        self,
        nas_ip: str,
        secret: str,
        session_id: Optional[str] = None,
        calling_station_id: Optional[str] = None,
        username: Optional[str] = None,
        nas_port: Optional[int] = None,
    ) -> CoAResult:
        """
        Send Disconnect-Request (RFC 5176) to terminate a RADIUS session.

        The NAS will immediately disconnect the port/session, forcing
        the device to re-authenticate from scratch.

        At least one session identifier is required:
        - session_id (Acct-Session-Id) - most reliable
        - calling_station_id (MAC address)
        - username + nas_port
        """
        pkt = RadiusPacket(
            code=RadiusCode.DISCONNECT_REQUEST,
            identifier=self._next_id(),
        )

        # Add session identifiers
        if session_id:
            pkt.add_string(RadiusAttr.ACCT_SESSION_ID, session_id)
        if calling_station_id:
            pkt.add_string(RadiusAttr.CALLING_STATION_ID, calling_station_id)
        if username:
            pkt.add_string(RadiusAttr.USER_NAME, username)
        if nas_port is not None:
            pkt.add_integer(RadiusAttr.NAS_PORT, nas_port)

        result = await self._send_packet(pkt, nas_ip, secret, "disconnect")
        result.session_id = session_id
        result.mac_address = calling_station_id
        return result

    async def reauthenticate_session(
        self,
        nas_ip: str,
        secret: str,
        session_id: Optional[str] = None,
        calling_station_id: Optional[str] = None,
        vendor: str = "cisco",
    ) -> CoAResult:
        """
        Send CoA-Request to force re-authentication.

        The switch will restart the 802.1X handshake for the session.
        The device stays connected but goes through EAP again, which
        means OpenRadiusWeb can apply updated policies.

        Implementation varies by vendor:
        - Cisco: Cisco-AVPair = "subscriber:command=reauthenticate"
        - Aruba: Cisco-AVPair = "subscriber:command=reauthenticate" (compatible)
        - Juniper: CoA with Service-Type = Authorize-Only
        """
        pkt = RadiusPacket(
            code=RadiusCode.COA_REQUEST,
            identifier=self._next_id(),
        )

        # Session identification
        if session_id:
            pkt.add_string(RadiusAttr.ACCT_SESSION_ID, session_id)
        if calling_station_id:
            pkt.add_string(RadiusAttr.CALLING_STATION_ID, calling_station_id)

        # Vendor-specific reauthenticate command
        vendor_lower = vendor.lower()
        if vendor_lower in ("cisco", "aruba", "hpe"):
            pkt.add_cisco_avpair("subscriber:command=reauthenticate")
        elif vendor_lower == "juniper":
            # Juniper uses Service-Type = Authorize-Only (17)
            pkt.add_integer(6, 17)  # Service-Type = 6
        else:
            # Generic: use Cisco AVPair (widely supported)
            pkt.add_cisco_avpair("subscriber:command=reauthenticate")

        result = await self._send_packet(pkt, nas_ip, secret, "reauthenticate")
        result.session_id = session_id
        result.mac_address = calling_station_id
        return result

    async def change_vlan(
        self,
        nas_ip: str,
        secret: str,
        vlan_id: int,
        session_id: Optional[str] = None,
        calling_station_id: Optional[str] = None,
        vendor: str = "cisco",
    ) -> CoAResult:
        """
        Send CoA-Request to change session VLAN without disconnecting.

        This is the most seamless CoA operation - the device's VLAN
        changes without interrupting the connection. However, the device
        needs to renew its IP address (DHCP) to get a new IP in the
        target VLAN.

        For Cisco, this is typically followed by a bounce-port or
        reauthenticate to force DHCP renewal.
        """
        pkt = RadiusPacket(
            code=RadiusCode.COA_REQUEST,
            identifier=self._next_id(),
        )

        # Session identification
        if session_id:
            pkt.add_string(RadiusAttr.ACCT_SESSION_ID, session_id)
        if calling_station_id:
            pkt.add_string(RadiusAttr.CALLING_STATION_ID, calling_station_id)

        # Add VLAN attributes
        pkt.add_tunnel_vlan(vlan_id)

        # Cisco: also need AVPair to trigger VLAN change
        if vendor.lower() in ("cisco",):
            pkt.add_cisco_avpair(f"audit-session-id={session_id or ''}")
            pkt.add_cisco_avpair("subscriber:command=reauthenticate")

        result = await self._send_packet(pkt, nas_ip, secret, f"vlan_change:{vlan_id}")
        result.session_id = session_id
        result.mac_address = calling_station_id
        return result

    async def apply_acl(
        self,
        nas_ip: str,
        secret: str,
        acl_name: str,
        session_id: Optional[str] = None,
        calling_station_id: Optional[str] = None,
    ) -> CoAResult:
        """
        Send CoA-Request to apply/change ACL on a session.

        Uses Filter-Id or Cisco dACL (downloadable ACL).
        """
        pkt = RadiusPacket(
            code=RadiusCode.COA_REQUEST,
            identifier=self._next_id(),
        )

        if session_id:
            pkt.add_string(RadiusAttr.ACCT_SESSION_ID, session_id)
        if calling_station_id:
            pkt.add_string(RadiusAttr.CALLING_STATION_ID, calling_station_id)

        # Apply ACL via Filter-Id
        pkt.add_string(RadiusAttr.FILTER_ID, acl_name)

        # Also send as Cisco dACL if Cisco switch
        pkt.add_cisco_avpair(f"ACS:CiscoSecure-Defined-ACL=#{acl_name}")

        result = await self._send_packet(pkt, nas_ip, secret, f"apply_acl:{acl_name}")
        result.session_id = session_id
        result.mac_address = calling_station_id
        return result

    # ============================================================
    # High-Level Operations (use session lookup from DB)
    # ============================================================

    async def coa_by_mac(
        self,
        mac_address: str,
        action: str,
        vlan_id: Optional[int] = None,
        acl_name: Optional[str] = None,
    ) -> CoAResult:
        """
        Perform CoA by MAC address - looks up active session in DB.

        Args:
            mac_address: Device MAC (any format: AA:BB:CC:DD:EE:FF)
            action: "disconnect" | "reauthenticate" | "vlan_change" | "apply_acl"
            vlan_id: Target VLAN (required for vlan_change)
            acl_name: ACL name (required for apply_acl)
        """
        session = await self._find_active_session(mac_address=mac_address)
        if not session:
            return CoAResult(
                success=False,
                action=action,
                nas_ip="",
                mac_address=mac_address,
                error_message=f"No active session found for MAC {mac_address}",
            )

        return await self._execute_coa(session, action, vlan_id, acl_name)

    async def coa_by_username(
        self,
        username: str,
        action: str,
        vlan_id: Optional[int] = None,
        acl_name: Optional[str] = None,
    ) -> CoAResult:
        """Perform CoA by username - disconnect/reauthenticate all sessions."""
        sessions = await self._find_active_sessions_by_user(username)
        if not sessions:
            return CoAResult(
                success=False,
                action=action,
                nas_ip="",
                error_message=f"No active sessions found for user {username}",
            )

        # Execute CoA for all active sessions
        results = []
        for session in sessions:
            result = await self._execute_coa(session, action, vlan_id, acl_name)
            results.append(result)

        # Return combined result
        all_success = all(r.success for r in results)
        return CoAResult(
            success=all_success,
            action=action,
            nas_ip=", ".join(r.nas_ip for r in results),
            error_message=None if all_success else
                f"{sum(1 for r in results if not r.success)}/{len(results)} sessions failed",
        )

    async def coa_by_session_id(
        self,
        session_id: str,
        action: str,
        vlan_id: Optional[int] = None,
        acl_name: Optional[str] = None,
    ) -> CoAResult:
        """Perform CoA by RADIUS session ID."""
        session = await self._find_active_session(session_id=session_id)
        if not session:
            return CoAResult(
                success=False,
                action=action,
                nas_ip="",
                session_id=session_id,
                error_message=f"Session {session_id} not found or not active",
            )

        return await self._execute_coa(session, action, vlan_id, acl_name)

    async def _execute_coa(
        self, session: dict, action: str,
        vlan_id: Optional[int] = None, acl_name: Optional[str] = None,
    ) -> CoAResult:
        """Execute CoA against a session record."""
        nas_ip = session["nas_ip"]
        secret = await self._get_coa_secret(nas_ip)
        vendor = session.get("vendor", "cisco")

        sid = session.get("session_id")
        mac = session.get("calling_station_id")

        if action == "disconnect":
            result = await self.disconnect_session(
                nas_ip, secret, session_id=sid, calling_station_id=mac)
        elif action == "reauthenticate":
            result = await self.reauthenticate_session(
                nas_ip, secret, session_id=sid, calling_station_id=mac, vendor=vendor)
        elif action == "vlan_change" and vlan_id is not None:
            result = await self.change_vlan(
                nas_ip, secret, vlan_id, session_id=sid, calling_station_id=mac, vendor=vendor)
        elif action == "apply_acl" and acl_name:
            result = await self.apply_acl(
                nas_ip, secret, acl_name, session_id=sid, calling_station_id=mac)
        else:
            return CoAResult(
                success=False, action=action, nas_ip=nas_ip,
                error_message=f"Unknown action '{action}' or missing parameters",
            )

        # Log CoA attempt to database
        await self._log_coa(session, result)

        # Publish event
        await nats_client.publish("orw.coa.result", result.to_dict())

        return result

    # ============================================================
    # Network I/O
    # ============================================================

    async def _send_packet(
        self, pkt: RadiusPacket, nas_ip: str, secret: str, action: str,
    ) -> CoAResult:
        """Send RADIUS packet and wait for response."""
        encoded = pkt.encode(secret)

        log.info("coa_sending",
                 action=action, nas_ip=nas_ip,
                 code=RadiusCode(pkt.code).name,
                 identifier=pkt.identifier)

        try:
            # Create UDP socket
            loop = asyncio.get_event_loop()
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _CoAProtocol(),
                remote_addr=(nas_ip, COA_PORT),
            )

            try:
                transport.sendto(encoded)

                # Wait for response with timeout
                response_data = await asyncio.wait_for(
                    protocol.response_future, timeout=COA_TIMEOUT
                )

                # Parse response
                response = RadiusPacket.decode(response_data, secret)
                response_code = response.code

                # Check response
                if response_code in (RadiusCode.COA_ACK, RadiusCode.DISCONNECT_ACK):
                    log.info("coa_success",
                             action=action, nas_ip=nas_ip,
                             response=RadiusCode(response_code).name)
                    return CoAResult(
                        success=True, action=action, nas_ip=nas_ip,
                        response_code=response_code,
                    )
                else:
                    error_cause = response.get_error_cause()
                    error_msg = ERROR_CAUSE.get(error_cause, "Unknown error") if error_cause else None
                    reply_msg = response.get_attr_string(RadiusAttr.REPLY_MESSAGE)

                    log.warning("coa_rejected",
                                action=action, nas_ip=nas_ip,
                                response=RadiusCode(response_code).name,
                                error_cause=error_cause,
                                error_message=error_msg or reply_msg)

                    return CoAResult(
                        success=False, action=action, nas_ip=nas_ip,
                        response_code=response_code,
                        error_cause=error_cause,
                        error_message=error_msg or reply_msg or "CoA rejected by NAS",
                    )

            finally:
                transport.close()

        except asyncio.TimeoutError:
            log.error("coa_timeout", action=action, nas_ip=nas_ip)
            return CoAResult(
                success=False, action=action, nas_ip=nas_ip,
                error_message=f"CoA timeout after {COA_TIMEOUT}s - NAS may not support CoA or secret mismatch",
            )
        except OSError as e:
            log.error("coa_network_error", action=action, nas_ip=nas_ip, error=str(e))
            return CoAResult(
                success=False, action=action, nas_ip=nas_ip,
                error_message=f"Network error: {e}",
            )

    # ============================================================
    # Database Operations
    # ============================================================

    async def _find_active_session(
        self,
        session_id: Optional[str] = None,
        mac_address: Optional[str] = None,
    ) -> Optional[dict]:
        """Find an active RADIUS session by session ID or MAC."""
        from sqlalchemy import text

        async with get_db_context() as db:
            if session_id:
                result = await db.execute(
                    text("""
                        SELECT rs.*, nd.vendor, nd.coa_secret_encrypted
                        FROM radius_sessions rs
                        LEFT JOIN network_devices nd ON rs.nas_ip::text = nd.ip_address::text
                        WHERE rs.session_id = :sid AND rs.status = 'active'
                    """),
                    {"sid": session_id},
                )
            elif mac_address:
                # Normalize MAC for matching
                mac_clean = mac_address.replace(":", "").replace("-", "").replace(".", "").lower()
                result = await db.execute(
                    text("""
                        SELECT rs.*, nd.vendor, nd.coa_secret_encrypted
                        FROM radius_sessions rs
                        LEFT JOIN network_devices nd ON rs.nas_ip::text = nd.ip_address::text
                        WHERE REPLACE(REPLACE(LOWER(rs.calling_station_id), ':', ''), '-', '') = :mac
                          AND rs.status = 'active'
                        ORDER BY rs.started_at DESC
                        LIMIT 1
                    """),
                    {"mac": mac_clean},
                )
            else:
                return None

            row = result.mappings().first()
            return dict(row) if row else None

    async def _find_active_sessions_by_user(self, username: str) -> list[dict]:
        """Find all active RADIUS sessions for a username."""
        from sqlalchemy import text

        async with get_db_context() as db:
            result = await db.execute(
                text("""
                    SELECT rs.*, nd.vendor, nd.coa_secret_encrypted
                    FROM radius_sessions rs
                    LEFT JOIN network_devices nd ON rs.nas_ip::text = nd.ip_address::text
                    WHERE rs.username ILIKE :username AND rs.status = 'active'
                """),
                {"username": f"%{username}%"},
            )
            return [dict(r) for r in result.mappings().all()]

    async def _get_coa_secret(self, nas_ip: str) -> str:
        """Get CoA shared secret for a NAS from database."""
        from sqlalchemy import text

        async with get_db_context() as db:
            result = await db.execute(
                text("""
                    SELECT coa_secret_encrypted
                    FROM network_devices
                    WHERE ip_address = :ip
                """),
                {"ip": nas_ip},
            )
            row = result.first()
            if row and row[0]:
                # TODO: Decrypt via Vault in production
                return row[0]

        log.warning("coa_secret_not_found", nas_ip=nas_ip)
        return "default_coa_secret"  # Fallback for dev

    async def _log_coa(self, session: dict, result: CoAResult):
        """Log CoA attempt to audit trail."""
        from sqlalchemy import text

        async with get_db_context() as db:
            await db.execute(
                text("""
                    INSERT INTO events (
                        event_type, severity, device_id, source, message, details
                    ) VALUES (
                        'coa', :severity, :device_id, 'coa_manager',
                        :message, :details::jsonb
                    )
                """),
                {
                    "severity": "info" if result.success else "warning",
                    "device_id": session.get("device_id"),
                    "message": f"CoA {result.action} to {result.nas_ip}: "
                               f"{'success' if result.success else 'failed'}",
                    "details": str(result.to_dict()).replace("'", '"'),
                },
            )

            # Update session status if disconnected
            if result.success and result.action == "disconnect":
                await db.execute(
                    text("""
                        UPDATE radius_sessions
                        SET status = 'terminated', ended_at = NOW()
                        WHERE session_id = :sid
                    """),
                    {"sid": session.get("session_id")},
                )


class _CoAProtocol(asyncio.DatagramProtocol):
    """UDP protocol for receiving CoA responses."""

    def __init__(self):
        self.response_future = asyncio.get_event_loop().create_future()

    def datagram_received(self, data: bytes, addr: tuple):
        if not self.response_future.done():
            self.response_future.set_result(data)

    def error_received(self, exc: Exception):
        if not self.response_future.done():
            self.response_future.set_exception(exc)

    def connection_lost(self, exc: Optional[Exception]):
        if not self.response_future.done() and exc:
            self.response_future.set_exception(exc or ConnectionError("Connection lost"))


# ============================================================
# NATS Event Handlers - Policy Engine Integration
# ============================================================

_coa_manager = CoAManager()


async def handle_coa_action(data: dict):
    """
    Handle CoA action from policy engine.

    Expected data:
    {
        "action": "reauthenticate" | "disconnect" | "vlan_change" | "apply_acl",
        "session_id": "...",       # optional
        "mac_address": "...",      # optional
        "username": "...",         # optional
        "vlan_id": 99,             # for vlan_change
        "acl_name": "quarantine",  # for apply_acl
    }
    """
    action = data.get("action", "reauthenticate")
    vlan_id = data.get("vlan_id")
    acl_name = data.get("acl_name")

    if data.get("session_id"):
        result = await _coa_manager.coa_by_session_id(
            data["session_id"], action, vlan_id, acl_name)
    elif data.get("mac_address"):
        result = await _coa_manager.coa_by_mac(
            data["mac_address"], action, vlan_id, acl_name)
    elif data.get("username"):
        result = await _coa_manager.coa_by_username(
            data["username"], action, vlan_id, acl_name)
    else:
        log.error("coa_missing_identifier", data=data)
        return

    log.info("coa_action_completed",
             action=action,
             success=result.success,
             nas_ip=result.nas_ip)


async def handle_policy_vlan_assign(data: dict):
    """
    Handle VLAN assignment from policy engine.
    If device already has an active session, send CoA to change VLAN.
    """
    mac_address = data.get("mac_address")
    new_vlan = data.get("vlan_id")

    if not mac_address or not new_vlan:
        return

    # Check if device has active session
    session = await _coa_manager._find_active_session(mac_address=mac_address)
    if session and session.get("assigned_vlan") != new_vlan:
        log.info("vlan_change_via_coa",
                 mac=mac_address,
                 old_vlan=session.get("assigned_vlan"),
                 new_vlan=new_vlan)

        result = await _coa_manager.change_vlan(
            nas_ip=session["nas_ip"],
            secret=await _coa_manager._get_coa_secret(session["nas_ip"]),
            vlan_id=new_vlan,
            session_id=session.get("session_id"),
            calling_station_id=mac_address,
            vendor=session.get("vendor", "cisco"),
        )

        if not result.success:
            # Fallback: disconnect and let device re-authenticate with new policy
            log.warning("coa_vlan_change_failed_fallback_disconnect",
                        mac=mac_address, error=result.error_message)
            await _coa_manager.disconnect_session(
                nas_ip=session["nas_ip"],
                secret=await _coa_manager._get_coa_secret(session["nas_ip"]),
                session_id=session.get("session_id"),
                calling_station_id=mac_address,
            )
