"""NATS publishers for the certificates feature (Layer 2).

Activating a certificate (CA or server) is the only mutation that
requires FreeRADIUS to reload its TLS material — same subject as the
``nas_clients`` and ``ldap_servers`` features, with a more specific
payload so subscribers can log *why* the apply was triggered.
"""
from orw_common import nats_client


SUBJECT_FREERADIUS_APPLY = "orw.config.freeradius.apply"


async def publish_freeradius_apply_for_cert(
    *, cert_id: str, cert_type: str, reason: str = "certificate_activated",
) -> None:
    await nats_client.publish(
        SUBJECT_FREERADIUS_APPLY,
        {"reason": reason, "cert_id": cert_id, "cert_type": cert_type},
    )
