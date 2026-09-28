"""Send representative incidents to a running local prototype server."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TEST_INCIDENTS: tuple[dict[str, Any], ...] = (
    {
        "incident_id": "INC-2001",
        "short_description": "Checkout service repeatedly crashes",
        "description": "Checkout pods started restarting shortly after the afternoon release.",
        "category": "Application",
        "priority": "P1",
        "raw_logs": "2026-09-17T10:04:12Z java.lang.OutOfMemoryError: Java heap space\nOOMKilled exit code 137",
        "recent_changes": ["Deployed checkout-api version 4.8.0", "Increased promotion traffic allocation"],
    },
    {
        "incident_id": "INC-2002",
        "short_description": "Order processing blocked by database pool",
        "description": "Order requests hang while all database connections remain occupied.",
        "category": "Database",
        "priority": "P2",
        "raw_logs": "ERROR connection pool exhausted: unable to acquire a connection\nERROR lock wait timeout exceeded",
        "recent_changes": ["Enabled a new hourly sales reporting query"],
    },
)


def post_json(url: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Prototype API base URL")
    args = parser.parse_args()
    endpoint = f"{args.base_url.rstrip('/')}/api/v1/prototype/ingest"
    try:
        for incident in TEST_INCIDENTS:
            print(f"\n=== {incident['incident_id']} ===")
            print(json.dumps(post_json(endpoint, incident), indent=2))
    except HTTPError as exc:
        print(f"API returned HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Could not reach {endpoint}: {exc.reason}. Start the API with: python main.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

