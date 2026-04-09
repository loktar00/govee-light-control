"""Govee REST API server — persistent BLE connections for instant control."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .protocol import (
    scan_devices,
    load_cache,
    resolve_device,
    format_device,
    get_all_devices,
    color_packet,
    brightness_packet,
    power_packet,
    white_packet,
    kelvin_to_warmth,
    parse_hex_color,
    GoveeError,
    PersistentPool,
)
from .effects import EFFECTS, run_effect_on_pool

pool: PersistentPool | None = None

DEFAULT_PORT = 8766


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _error(msg, status=400):
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


async def _get_body(request: Request) -> dict:
    """Parse JSON body, returning empty dict for bodyless requests."""
    body = await request.body()
    if not body:
        return {}
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {}


async def _resolve(data: dict):
    """Resolve device from request body."""
    arg = data.get("device") or None
    return await resolve_device(arg)


# ─── Device Endpoints ────────────────────────────────────────────────────────

async def api_devices(request: Request):
    """GET /api/devices — list cached devices with connection status."""
    devices = load_cache()
    connected = pool.connected_addresses() if pool else set()
    result = []
    for i, dev in enumerate(devices):
        d = dict(dev)
        d["index"] = i + 1
        d["connected"] = dev["address"] in connected
        result.append(d)
    return JSONResponse({"ok": True, "devices": result, "count": len(result)})


async def api_scan(request: Request):
    """POST /api/scan — trigger BLE scan, reconnect pool."""
    global pool
    devices = await scan_devices()
    if pool:
        await pool.stop()
    if devices:
        pool = PersistentPool(devices)
        await pool.start()
    return JSONResponse({"ok": True, "devices": devices, "count": len(devices)})


# ─── Single-Device Commands ──────────────────────────────────────────────────

async def api_on(request: Request):
    """POST /api/on — power on a device."""
    try:
        data = await _get_body(request)
        dev = await _resolve(data)
        await pool.send(dev["address"], power_packet(True))
        return JSONResponse({"ok": True, "device": dev["address"], "action": "on"})
    except GoveeError as e:
        return _error(str(e))


async def api_off(request: Request):
    """POST /api/off — power off a device."""
    try:
        data = await _get_body(request)
        dev = await _resolve(data)
        await pool.send(dev["address"], power_packet(False))
        return JSONResponse({"ok": True, "device": dev["address"], "action": "off"})
    except GoveeError as e:
        return _error(str(e))


async def api_color(request: Request):
    """POST /api/color — set RGB color."""
    try:
        data = await _get_body(request)
        hex_str = data.get("hex", "")
        if not hex_str:
            return _error("Missing 'hex' field (e.g. \"#ff0000\")")
        r, g, b = parse_hex_color(hex_str)
        dev = await _resolve(data)
        await pool.send(dev["address"], color_packet(r, g, b))
        return JSONResponse({"ok": True, "device": dev["address"], "color": f"#{hex_str.lstrip('#')}"})
    except GoveeError as e:
        return _error(str(e))


async def api_brightness(request: Request):
    """POST /api/brightness — set brightness 1-100."""
    try:
        data = await _get_body(request)
        value = data.get("value")
        if value is None or not (1 <= int(value) <= 100):
            return _error("'value' must be 1-100")
        dev = await _resolve(data)
        await pool.send(dev["address"], brightness_packet(int(value)))
        return JSONResponse({"ok": True, "device": dev["address"], "brightness": int(value)})
    except GoveeError as e:
        return _error(str(e))


async def api_white(request: Request):
    """POST /api/white — set white LED temperature."""
    try:
        data = await _get_body(request)
        kelvin = int(data.get("kelvin", 4000))
        if not (2700 <= kelvin <= 6500):
            return _error("'kelvin' must be 2700-6500")
        dev = await _resolve(data)
        warmth = kelvin_to_warmth(kelvin)
        await pool.send(dev["address"], white_packet(warmth))
        return JSONResponse({"ok": True, "device": dev["address"], "temperature": kelvin})
    except GoveeError as e:
        return _error(str(e))


async def api_status(request: Request):
    """POST /api/status — query device state."""
    try:
        data = await _get_body(request)
        dev = await _resolve(data)
        connected = pool.connected_addresses() if pool else set()
        return JSONResponse({
            "ok": True,
            "device": dev["address"],
            "name": dev.get("name", ""),
            "connected": dev["address"] in connected,
        })
    except GoveeError as e:
        return _error(str(e))


# ─── Batch Endpoints ─────────────────────────────────────────────────────────

async def api_all_on(request: Request):
    """POST /api/all/on — all lights on."""
    ok, fail = await pool.send_all(power_packet(True))
    return JSONResponse({"ok": True, "action": "on", "succeeded": ok, "failed": fail})


async def api_all_off(request: Request):
    """POST /api/all/off — all lights off."""
    ok, fail = await pool.send_all(power_packet(False))
    return JSONResponse({"ok": True, "action": "off", "succeeded": ok, "failed": fail})


async def api_all_color(request: Request):
    """POST /api/all/color — set all lights to same color."""
    try:
        data = await _get_body(request)
        hex_str = data.get("hex", "")
        if not hex_str:
            return _error("Missing 'hex' field")
        r, g, b = parse_hex_color(hex_str)
        ok, fail = await pool.send_all(color_packet(r, g, b))
        return JSONResponse({"ok": True, "action": "color", "color": f"#{hex_str.lstrip('#')}", "succeeded": ok, "failed": fail})
    except GoveeError as e:
        return _error(str(e))


async def api_all_white(request: Request):
    """POST /api/all/white — set all lights to same white temperature."""
    try:
        data = await _get_body(request)
        kelvin = int(data.get("kelvin", 4000))
        if not (2700 <= kelvin <= 6500):
            return _error("'kelvin' must be 2700-6500")
        warmth = kelvin_to_warmth(kelvin)
        ok, fail = await pool.send_all(white_packet(warmth))
        return JSONResponse({"ok": True, "action": "white", "temperature": kelvin, "succeeded": ok, "failed": fail})
    except GoveeError as e:
        return _error(str(e))


async def api_all_brightness(request: Request):
    """POST /api/all/brightness — set all lights to same brightness."""
    try:
        data = await _get_body(request)
        value = data.get("value")
        if value is None or not (1 <= int(value) <= 100):
            return _error("'value' must be 1-100")
        ok, fail = await pool.send_all(brightness_packet(int(value)))
        return JSONResponse({"ok": True, "action": "brightness", "brightness": int(value), "succeeded": ok, "failed": fail})
    except GoveeError as e:
        return _error(str(e))


# ─── Effects ────────────────────────────────────────────────────────────────

async def api_effects(request: Request):
    """GET /api/effects — list available effects."""
    return JSONResponse({"ok": True, "effects": EFFECTS})


async def api_effect(request: Request):
    """POST /api/effect — run a lighting effect on all devices.

    Body: {"name": "spectrum", "duration": 10, "speed": 1.0, "color": "#ff0000", "origin": "1"}
    Only "name" is required.
    """
    if not pool or not pool.clients:
        return _error("No connected devices. POST /api/scan first.", 503)
    try:
        data = await _get_body(request)
        name = data.get("name", "")
        if not name or name not in EFFECTS:
            return _error(
                f"Unknown effect: {name!r}. Available: {', '.join(EFFECTS.keys())}")
        duration = float(data.get("duration", 10))
        speed = float(data.get("speed", 1.0))
        color = data.get("color") or None
        origin = data.get("origin") or None
        devices = get_all_devices()
        result = await run_effect_on_pool(
            pool, devices, name,
            duration=duration, speed=speed, origin=origin, color=color)
        return JSONResponse({"ok": True, **result})
    except GoveeError as e:
        return _error(str(e))


# ─── Lifecycle ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    global pool
    devices = load_cache()
    if not devices:
        print("No cached devices. Run 'govee scan' first, or POST /api/scan")
    else:
        print(f"Connecting to {len(devices)} device(s)...")
        pool = PersistentPool(devices)
        ok, total = await pool.start()
        print(f"Connected: {ok}/{total}")
    yield
    if pool:
        await pool.stop()
    print("Pool stopped.")


# ─── App ─────────────────────────────────────────────────────────────────────

routes = [
    Route("/api/devices", api_devices, methods=["GET"]),
    Route("/api/scan", api_scan, methods=["POST"]),
    Route("/api/on", api_on, methods=["POST"]),
    Route("/api/off", api_off, methods=["POST"]),
    Route("/api/color", api_color, methods=["POST"]),
    Route("/api/brightness", api_brightness, methods=["POST"]),
    Route("/api/white", api_white, methods=["POST"]),
    Route("/api/status", api_status, methods=["POST"]),
    Route("/api/all/on", api_all_on, methods=["POST"]),
    Route("/api/all/off", api_all_off, methods=["POST"]),
    Route("/api/all/color", api_all_color, methods=["POST"]),
    Route("/api/all/white", api_all_white, methods=["POST"]),
    Route("/api/all/brightness", api_all_brightness, methods=["POST"]),
    Route("/api/effects", api_effects, methods=["GET"]),
    Route("/api/effect", api_effect, methods=["POST"]),
]

app = Starlette(routes=routes, lifespan=lifespan)


def main():
    """Entry point for govee-server console script."""
    import uvicorn

    port = DEFAULT_PORT
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--port" and i < len(sys.argv) - 1:
            port = int(sys.argv[i + 1])

    print(f"Govee REST API server starting on http://0.0.0.0:{port}")
    print(f"Endpoints: GET /api/devices, POST /api/on, /api/off, /api/color, ...")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
