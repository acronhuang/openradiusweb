"""
FreeRADIUS Configuration Watcher - Long-lived daemon for config synchronization.

Responsibilities:
1. Subscribe to NATS JetStream subject ``orw.config.freeradius.apply``
2. On message, regenerate FreeRADIUS configs from database and write to disk
3. Send SIGHUP to the FreeRADIUS container to reload configuration
4. Run periodic reconciliation every 5 minutes to catch missed events

Run as:
    python freeradius_config_watcher.py
"""

import asyncio
import json
import os
import signal
import subprocess
import traceback

import nats

from freeradius_config_manager import FreeRADIUSConfigManager


# ============================================================
# Configuration
# ============================================================

FREERADIUS_CONTAINER = os.environ.get("FREERADIUS_CONTAINER", "orw-freeradius")
NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
NATS_STREAM = "orw"
NATS_SUBJECT = "orw.config.freeradius.apply"
NATS_DURABLE = "freeradius-config-watcher"
RECONCILE_INTERVAL = int(os.environ.get("ORW_RECONCILE_INTERVAL", "300"))  # seconds

DB_URL = os.environ.get("ORW_DB_URL", "")
TEMPLATE_DIR = os.environ.get(
    "ORW_TEMPLATE_DIR", "/etc/freeradius/orw-templates"
)
OUTPUT_DIR = os.environ.get(
    "ORW_OUTPUT_DIR", "/etc/freeradius/orw-managed"
)
CERT_DIR = os.environ.get(
    "ORW_CERT_DIR", "/etc/freeradius/certs"
)


# ============================================================
# FreeRADIUS reload
# ============================================================

def reload_freeradius() -> bool:
    """
    Send SIGHUP to FreeRADIUS to reload configuration.

    FreeRADIUS re-reads its configuration files on SIGHUP without
    dropping active sessions. This is the safest way to apply config
    changes at runtime.

    Implementation note: prefers the Python docker SDK (sends SIGHUP
    via the container API directly) over `docker exec kill -HUP 1`.
    The exec-based approach used to fail with `exit=127` because
    `kill` isn't always on PATH inside the slim freeradius image —
    debian:bookworm-slim drops some coreutils binaries. The API call
    doesn't run anything inside the target container, just signals
    the main process via Docker daemon.

    Falls back to subprocess docker CLI if the SDK isn't installed
    (older deploys without the PR #78 Dockerfile bump).

    Returns:
        True if the reload command succeeded, False otherwise.
    """
    # Prefer SDK path — no binary lookup inside target container.
    try:
        import docker

        client = docker.from_env()
        container = client.containers.get(FREERADIUS_CONTAINER)
        container.kill(signal="SIGHUP")
        print(
            f"[config-watcher] FreeRADIUS reloaded "
            f"(SIGHUP via docker API to {FREERADIUS_CONTAINER})"
        )
        return True
    except ImportError:
        # SDK not installed (pre-#78 image). Fall through to subprocess.
        pass
    except Exception as e:
        # Network / permission / container-not-found errors. Log and
        # fall through — subprocess might still succeed via /proc/self/...
        # if for some reason the SDK is broken.
        print(
            f"[config-watcher] docker SDK reload failed ({type(e).__name__}: "
            f"{e}); falling back to subprocess docker CLI."
        )

    # Fallback: subprocess docker CLI (legacy path, pre-PR #78).
    try:
        subprocess.run(
            ["docker", "exec", FREERADIUS_CONTAINER, "kill", "-HUP", "1"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        print(
            f"[config-watcher] FreeRADIUS reloaded "
            f"(HUP via subprocess docker exec to {FREERADIUS_CONTAINER})"
        )
        return True
    except subprocess.CalledProcessError as e:
        print(
            f"[config-watcher] Failed to reload FreeRADIUS: "
            f"exit={e.returncode} stderr={e.stderr.strip()}"
        )
        return False
    except subprocess.TimeoutExpired:
        print("[config-watcher] Timeout sending HUP to FreeRADIUS container")
        return False
    except FileNotFoundError:
        print(
            "[config-watcher] 'docker' command not found and SDK not "
            "available. Ensure docker.io + python `docker` package are "
            "installed, or set FREERADIUS_CONTAINER='' to disable reload."
        )
        return False


def apply_and_reload(manager: FreeRADIUSConfigManager, reason: str = "") -> dict:
    """
    Apply configs from database and reload FreeRADIUS.

    Args:
        manager: The FreeRADIUSConfigManager instance.
        reason: Human-readable reason for the apply (for logging).

    Returns:
        Dict with apply results plus a ``reload`` key.
    """
    context = f" (reason: {reason})" if reason else ""
    print(f"[config-watcher] Applying FreeRADIUS configuration{context}...")

    try:
        result = manager.apply_configs()

        errors = [k for k, v in result.items() if v.get("status") == "error"]
        applied = [k for k, v in result.items() if v.get("status") == "applied"]

        if errors:
            print(
                f"[config-watcher] Config applied with {len(errors)} error(s): "
                f"{', '.join(errors)}"
            )
        else:
            print(f"[config-watcher] All {len(applied)} configs applied successfully.")

        # Reload FreeRADIUS even if some configs had errors -- the configs
        # that succeeded should still be picked up.
        if applied:
            reload_ok = reload_freeradius()
            result["_reload"] = {
                "status": "ok" if reload_ok else "error",
                "error": None if reload_ok else "HUP signal failed",
            }
        else:
            result["_reload"] = {"status": "skipped", "error": "No configs applied"}

        return result

    except Exception as e:
        print(f"[config-watcher] ERROR during apply: {e}")
        traceback.print_exc()
        return {
            "_error": {
                "status": "error",
                "hash": "",
                "error": str(e),
            }
        }


# ============================================================
# NATS subscriber
# ============================================================

async def main():
    """
    Main event loop for the config watcher daemon.

    Connects to NATS JetStream, subscribes to config apply messages, and
    runs periodic reconciliation.
    """
    manager = FreeRADIUSConfigManager(
        db_url=DB_URL,
        template_dir=TEMPLATE_DIR,
        output_dir=OUTPUT_DIR,
        cert_dir=CERT_DIR,
    )

    # --- Initial apply on startup ---
    print("[config-watcher] Running initial config apply on startup...")
    apply_and_reload(manager, reason="startup")

    # --- Connect to NATS ---
    print(f"[config-watcher] Connecting to NATS at {NATS_URL}...")
    nc = await nats.connect(
        NATS_URL,
        reconnect_time_wait=2,
        max_reconnect_attempts=-1,  # Retry forever
    )
    js = nc.jetstream()

    # Ensure the stream exists
    try:
        await js.find_stream_by_subject(NATS_SUBJECT)
        print(f"[config-watcher] Found existing stream for {NATS_SUBJECT}")
    except Exception:
        print(f"[config-watcher] Creating stream '{NATS_STREAM}'...")
        await js.add_stream(
            name=NATS_STREAM,
            subjects=["orw.>"],
        )

    # Delete stale consumer first to avoid config conflicts.
    # This pattern comes from the project convention for single-instance
    # services -- see nats_client.py.
    try:
        stream_name = await js.find_stream_name_by_subject(NATS_SUBJECT)
        await js.delete_consumer(stream_name, NATS_DURABLE)
        print(f"[config-watcher] Deleted stale consumer '{NATS_DURABLE}'")
    except Exception:
        pass  # Consumer may not exist yet

    await asyncio.sleep(0.3)

    # Subscribe with durable consumer (pull-based via next_msg)
    sub = await js.subscribe(
        NATS_SUBJECT,
        durable=NATS_DURABLE,
    )
    print(
        f"[config-watcher] Subscribed to {NATS_SUBJECT} "
        f"(durable={NATS_DURABLE}). Listening for config changes..."
    )

    # --- Graceful shutdown ---
    stop_event = asyncio.Event()

    def _signal_handler():
        print("[config-watcher] Shutdown signal received...")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # signal.SIGTERM / add_signal_handler not supported on Windows
            pass

    # --- Main loop: receive messages + periodic reconciliation ---
    print(
        f"[config-watcher] Reconciliation interval: {RECONCILE_INTERVAL}s. "
        f"Waiting for messages..."
    )

    while not stop_event.is_set():
        try:
            msg = await asyncio.wait_for(
                sub.next_msg(),
                timeout=RECONCILE_INTERVAL,
            )

            # Process the message
            await msg.ack()

            # Parse message payload for logging context
            reason = ""
            try:
                payload = json.loads(msg.data.decode("utf-8"))
                reason = payload.get("reason", payload.get("action", "nats_event"))
                requested_by = payload.get("requested_by", "")
                if requested_by:
                    reason += f" by {requested_by}"
            except (json.JSONDecodeError, UnicodeDecodeError):
                reason = "nats_event"

            print(
                f"[config-watcher] Received apply request on "
                f"{msg.subject} (reason: {reason})"
            )

            result = apply_and_reload(manager, reason=reason)
            _log_result_summary(result)

        except asyncio.TimeoutError:
            # No message received within RECONCILE_INTERVAL -- run reconciliation
            print(
                f"[config-watcher] Periodic reconciliation "
                f"({RECONCILE_INTERVAL}s elapsed)..."
            )
            try:
                result = apply_and_reload(manager, reason="periodic_reconciliation")
                _log_result_summary(result)
            except Exception as e:
                print(f"[config-watcher] Reconciliation error: {e}")
                traceback.print_exc()

        except nats.errors.ConnectionClosedError:
            print("[config-watcher] NATS connection closed, attempting reconnect...")
            await asyncio.sleep(2)
            try:
                nc = await nats.connect(
                    NATS_URL,
                    reconnect_time_wait=2,
                    max_reconnect_attempts=-1,
                )
                js = nc.jetstream()

                # Re-subscribe after reconnect
                try:
                    stream_name = await js.find_stream_name_by_subject(NATS_SUBJECT)
                    await js.delete_consumer(stream_name, NATS_DURABLE)
                except Exception:
                    pass
                await asyncio.sleep(0.3)

                sub = await js.subscribe(
                    NATS_SUBJECT,
                    durable=NATS_DURABLE,
                )
                print("[config-watcher] Reconnected to NATS and re-subscribed.")
            except Exception as e:
                print(f"[config-watcher] NATS reconnect failed: {e}")
                await asyncio.sleep(5)

        except Exception as e:
            # Catch-all to keep the daemon running
            print(f"[config-watcher] Unexpected error in main loop: {e}")
            traceback.print_exc()
            await asyncio.sleep(2)

    # --- Shutdown ---
    print("[config-watcher] Shutting down...")
    try:
        await sub.unsubscribe()
    except Exception:
        pass
    try:
        await nc.close()
    except Exception:
        pass
    print("[config-watcher] Stopped.")


def _log_result_summary(result: dict) -> None:
    """Print a compact summary of apply results."""
    for config_type, info in result.items():
        if config_type.startswith("_"):
            continue
        status = info.get("status", "unknown")
        error = info.get("error")
        if error:
            print(f"  [{config_type}] {status} -- {error}")
        else:
            hash_short = info.get("hash", "")[:12]
            suffix = f" (hash: {hash_short}...)" if hash_short else ""
            print(f"  [{config_type}] {status}{suffix}")


# ============================================================
# Entrypoint
# ============================================================

if __name__ == "__main__":
    print(
        f"[config-watcher] OpenRadiusWeb FreeRADIUS Config Watcher starting...\n"
        f"  NATS_URL:              {NATS_URL}\n"
        f"  DB_URL:                {DB_URL.split('@')[0]}@***\n"
        f"  TEMPLATE_DIR:          {TEMPLATE_DIR}\n"
        f"  OUTPUT_DIR:            {OUTPUT_DIR}\n"
        f"  CERT_DIR:              {CERT_DIR}\n"
        f"  FREERADIUS_CONTAINER:  {FREERADIUS_CONTAINER}\n"
        f"  RECONCILE_INTERVAL:    {RECONCILE_INTERVAL}s\n"
    )
    asyncio.run(main())
