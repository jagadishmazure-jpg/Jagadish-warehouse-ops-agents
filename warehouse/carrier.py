"""External carrier-ETA signal client (mirrors the weather/demand pattern).

``FakeCarrier`` is deterministic and used in tests/demos. ``LiveCarrierBackend``
fetches a carrier tracking feed over HTTP; any failure returns an explicit
gap result -- the planner reports the gap instead of hallucinating an ETA.
"""

import hashlib
import json
import urllib.request


class FakeCarrier:
    """Deterministic delay signal derived from carrier+shipment."""

    def __init__(self, seed="warehouse-carrier"):
        self.seed = seed

    def get_signal(self, carrier, shipment_id):
        digest = hashlib.sha256(
            f"{self.seed}|{carrier}|{shipment_id}".encode()
        ).hexdigest()
        delay_days = int(digest[:4], 16) % 4  # 0..3
        return {
            "ok": True,
            "carrier": carrier,
            "shipment_id": shipment_id,
            "delay_days": delay_days,
            "status": "delayed" if delay_days else "on_time",
            "source": "fake",
        }


class LiveCarrierBackend:
    """HTTP tracking-feed stub. Configure with the real feed URL in production."""

    def __init__(self, url, timeout=5):
        self.url = url
        self.timeout = timeout

    def get_signal(self, carrier, shipment_id):
        try:
            req = urllib.request.Request(
                f"{self.url}?carrier={carrier}&shipment={shipment_id}",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return {
                "ok": True,
                "carrier": carrier,
                "shipment_id": shipment_id,
                "delay_days": int(payload.get("delay_days", 0)),
                "status": str(payload.get("status", "unknown")),
                "source": "live",
            }
        except Exception as exc:  # never let a feed outage become an answer
            return {
                "ok": False,
                "carrier": carrier,
                "shipment_id": shipment_id,
                "delay_days": None,
                "status": None,
                "source": "live",
                "gap": f"carrier signal unavailable: {exc.__class__.__name__}",
            }
