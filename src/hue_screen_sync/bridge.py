"""Hue Bridge auto-discovery and pairing."""

import json
import urllib.error
import urllib.request


DISCOVERY_URL = "https://discovery.meethue.com/"


def discover_bridges() -> list[dict]:
    """Find Hue bridges via Philips cloud discovery (contacts discovery.meethue.com)."""
    try:
        req = urllib.request.Request(DISCOVERY_URL, method="GET")
        resp = urllib.request.urlopen(req, timeout=5)
        return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return []


def create_api_user(bridge_ip: str) -> str | None:
    """Register a new API user. Press the bridge button first."""
    body = json.dumps({"devicetype": "hue-screen-sync#linux"}).encode()
    url = f"http://{bridge_ip}/api"
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read())
        if isinstance(result, list) and result:
            if "success" in result[0]:
                return result[0]["success"]["username"]
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        pass
    return None


def get_color_lights(bridge_ip: str, api_user: str) -> list[dict]:
    """Return all color-capable lights on the bridge."""
    url = f"http://{bridge_ip}/api/{api_user}/lights"
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        data = json.loads(resp.read())
        lights = []
        for lid, info in data.items():
            if info.get("type", "").lower() in ("extended color light", "color light"):
                lights.append({"id": int(lid), "name": info.get("name", f"Light {lid}")})
        return sorted(lights, key=lambda l: l["id"])
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return []
