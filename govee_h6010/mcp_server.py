"""Govee H6010 MCP server — AI agent integration via Model Context Protocol."""

import asyncio

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    raise ImportError(
        "MCP support requires the 'mcp' package. Install with: pip install govee-h6010[mcp]"
    )

from .protocol import (
    scan_devices as _scan_devices,
    send_command,
    send_to_all_simple,
    load_cache,
    resolve_device,
    format_device,
    color_packet,
    brightness_packet,
    power_packet,
    white_packet,
    kelvin_to_warmth,
    parse_hex_color,
    get_all_devices,
    GoveeError,
    GOVEE_WRITE_UUID,
    KEEPALIVE_BYTES,
)
from .effects import EFFECTS, run_effect as _run_effect

mcp = FastMCP("govee-h6010", host="0.0.0.0", port=8765)


@mcp.tool()
async def scan_devices() -> dict:
    """Scan for Govee H6010 BLE devices nearby. Saves results to cache for future commands."""
    devices = await _scan_devices()
    return {"ok": True, "devices": devices, "count": len(devices)}


@mcp.tool()
async def list_devices() -> dict:
    """List cached Govee devices (no BLE scan needed). Returns devices from last scan."""
    devices = load_cache()
    return {"ok": True, "devices": devices, "count": len(devices)}


@mcp.tool()
async def list_effects() -> dict:
    """List all available light effects with descriptions."""
    return {"ok": True, "effects": EFFECTS}


@mcp.tool()
async def power_on(device: str = "") -> dict:
    """Turn on a Govee light. Device can be MAC address, name suffix, or index number. Empty string uses first/only device."""
    try:
        dev = await resolve_device(device or None)
        await send_command(dev["address"], power_packet(True))
        return {"ok": True, "device": dev["address"], "action": "on"}
    except GoveeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def power_off(device: str = "") -> dict:
    """Turn off a Govee light. Device can be MAC address, name suffix, or index number."""
    try:
        dev = await resolve_device(device or None)
        await send_command(dev["address"], power_packet(False))
        return {"ok": True, "device": dev["address"], "action": "off"}
    except GoveeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def set_brightness(value: int, device: str = "") -> dict:
    """Set brightness on a Govee light. Value: 1-100. Device: MAC address, name, or index."""
    if value < 1 or value > 100:
        return {"ok": False, "error": "Brightness must be 1-100"}
    try:
        dev = await resolve_device(device or None)
        await send_command(dev["address"], brightness_packet(value))
        return {"ok": True, "device": dev["address"], "brightness": value}
    except GoveeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def set_color(hex_color: str, device: str = "") -> dict:
    """Set RGB color on a Govee light. hex_color: '#ff0000' or 'ff0000'. Device: MAC, name, or index."""
    try:
        r, g, b = parse_hex_color(hex_color)
        dev = await resolve_device(device or None)
        await send_command(dev["address"], color_packet(r, g, b))
        return {"ok": True, "device": dev["address"], "color": f"#{hex_color.lstrip('#')}"}
    except GoveeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def set_white(kelvin: int = 4000, device: str = "") -> dict:
    """Set white LED temperature on a Govee light. kelvin: 2700 (warm) to 6500 (cool). Uses dedicated white LEDs."""
    if kelvin < 2700 or kelvin > 6500:
        return {"ok": False, "error": "Temperature must be 2700-6500"}
    try:
        dev = await resolve_device(device or None)
        warmth = kelvin_to_warmth(kelvin)
        await send_command(dev["address"], white_packet(warmth))
        return {"ok": True, "device": dev["address"], "temperature": kelvin}
    except GoveeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def all_on() -> dict:
    """Turn on ALL Govee lights."""
    try:
        devices = get_all_devices()
        ok, fail, _ = await send_to_all_simple(devices, power_packet(True))
        return {"ok": True, "action": "on", "succeeded": ok, "failed": fail}
    except GoveeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def all_off() -> dict:
    """Turn off ALL Govee lights."""
    try:
        devices = get_all_devices()
        ok, fail, _ = await send_to_all_simple(devices, power_packet(False))
        return {"ok": True, "action": "off", "succeeded": ok, "failed": fail}
    except GoveeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def all_color(hex_color: str) -> dict:
    """Set RGB color on ALL Govee lights. hex_color: '#ff0000' or 'ff0000'."""
    try:
        r, g, b = parse_hex_color(hex_color)
        devices = get_all_devices()
        ok, fail, _ = await send_to_all_simple(devices, color_packet(r, g, b))
        return {"ok": True, "action": "color", "color": f"#{hex_color.lstrip('#')}", "succeeded": ok, "failed": fail}
    except GoveeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def all_white(kelvin: int = 4000) -> dict:
    """Set white LED temperature on ALL Govee lights. kelvin: 2700 (warm) to 6500 (cool)."""
    if kelvin < 2700 or kelvin > 6500:
        return {"ok": False, "error": "Temperature must be 2700-6500"}
    try:
        devices = get_all_devices()
        warmth = kelvin_to_warmth(kelvin)
        ok, fail, _ = await send_to_all_simple(devices, white_packet(warmth))
        return {"ok": True, "action": "white", "temperature": kelvin, "succeeded": ok, "failed": fail}
    except GoveeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def run_effect(name: str, duration: int = 10, speed: float = 1.0, color: str = "", origin: str = "") -> dict:
    """Run a light effect on all devices. Effects: spectrum, wave, breathe, party, candle, sunrise, ripple, chase, rain, wipe. Duration in seconds (default 10). Speed multiplier (default 1.0)."""
    if name not in EFFECTS:
        return {"ok": False, "error": f"Unknown effect: {name}. Available: {', '.join(EFFECTS.keys())}"}
    try:
        result = await _run_effect(
            name,
            duration=duration,
            speed=speed,
            origin=origin or None,
            color=color or None,
            quiet=True,
        )
        return {"ok": True, **result}
    except GoveeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def flash_device(device: str = "", seconds: int = 5) -> dict:
    """Flash a specific device bright white for identification. Turns on, goes bright white, waits, then turns off."""
    try:
        from bleak import BleakClient
        dev = await resolve_device(device or None)
        async with BleakClient(dev["address"], timeout=10) as client:
            await client.write_gatt_char(GOVEE_WRITE_UUID, KEEPALIVE_BYTES, response=False)
            await asyncio.sleep(0.05)
            await client.write_gatt_char(GOVEE_WRITE_UUID, power_packet(True), response=False)
            await asyncio.sleep(0.05)
            await client.write_gatt_char(GOVEE_WRITE_UUID, brightness_packet(100), response=False)
            await asyncio.sleep(0.05)
            await client.write_gatt_char(GOVEE_WRITE_UUID, white_packet(0x01), response=False)
            await asyncio.sleep(seconds)
            await client.write_gatt_char(GOVEE_WRITE_UUID, power_packet(False), response=False)
        return {"ok": True, "device": dev["address"], "name": dev.get("name", ""), "flashed_seconds": seconds}
    except GoveeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    """Entry point for govee-mcp console script."""
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
