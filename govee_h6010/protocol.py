"""Govee H6010 BLE protocol — packets, connections, device management."""

import asyncio
import colorsys
import json
import re
from pathlib import Path

from bleak import BleakScanner, BleakClient

# ─── Constants ────────────────────────────────────────────────────────────────

GOVEE_WRITE_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
GOVEE_READ_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"
CACHE_PATH = Path.home() / ".govee-ble-devices.json"
MAP_PATH = Path.home() / ".govee-device-map.json"
SCAN_TIMEOUT = 8
KEEPALIVE_BYTES = bytes([0xAA, 0x01] + [0x00] * 17 + [0xAB])
MAX_CONCURRENT = 5


# ─── Exceptions ───────────────────────────────────────────────────────────────

class GoveeError(Exception):
    pass


# ─── Packet Builders ─────────────────────────────────────────────────────────

def make_packet(cmd, *data):
    """Build a 20-byte Govee BLE packet with XOR checksum."""
    payload = [cmd] + list(data)
    payload += [0x00] * (19 - len(payload))
    checksum = 0
    for b in payload:
        checksum ^= b
    payload.append(checksum)
    return bytes(payload)


def color_packet(r, g, b):
    """RGB color packet (mode 0x0D for H6010)."""
    return make_packet(0x33, 0x05, 0x0D, r, g, b)


def brightness_packet(val):
    """Brightness packet (1-100)."""
    return make_packet(0x33, 0x04, val)


def power_packet(on):
    """Power on/off packet."""
    return make_packet(0x33, 0x01, 0x01 if on else 0x00)


def white_packet(warmth=0x01):
    """White LED packet. warmth: 0x01=warmest(~2700K) to 0xFF=coolest(~6500K)."""
    return make_packet(0x33, 0x05, 0x0D, 0x00, 0x00, 0x00, warmth, 0xFF)


def kelvin_to_warmth(kelvin):
    """Convert Kelvin (2700-6500) to warmth byte (0x01-0xFF)."""
    kelvin = max(2700, min(6500, kelvin))
    return max(1, int(1 + (kelvin - 2700) / (6500 - 2700) * 254))


def parse_hex_color(hex_str):
    """Parse #rrggbb or rrggbb to (r, g, b). Raises GoveeError on invalid input."""
    h = hex_str.lstrip("#")
    if len(h) != 6:
        raise GoveeError(f"Invalid hex color: {hex_str}")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        raise GoveeError(f"Invalid hex color: {hex_str}")
    return r, g, b


def hsv_to_rgb(h, s, v):
    """Convert HSV (0-1 range) to RGB (0-255)."""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


# ─── Device Cache ─────────────────────────────────────────────────────────────

def load_cache():
    """Load cached devices from disk."""
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return []


def save_cache(devices):
    """Save devices to disk cache."""
    CACHE_PATH.write_text(json.dumps(devices, indent=2))


def load_map():
    """Load device map. Returns dict with 'type' and position data, or empty dict."""
    try:
        data = json.loads(MAP_PATH.read_text())
        if isinstance(data, list):
            return {"type": "1d", "order": data}
        return data
    except Exception:
        return {}


def save_map(data):
    """Save device map to disk."""
    MAP_PATH.write_text(json.dumps(data, indent=2))


def parse_grid(text):
    """Parse an ASCII grid into 2D positions for each device number.

    Grid uses device numbers (1-99) and any non-digit as empty space.
    Returns dict of {device_index: (x, y)}.
    """
    positions = {}
    lines = text.strip().split("\n")
    for row, line in enumerate(lines):
        for match in re.finditer(r'\d+', line):
            num = int(match.group())
            col = match.start()
            positions[num] = (col, row)
    return positions


def get_mapped_devices():
    """Return devices in mapped order. Falls back to cache order."""
    devices = load_cache()
    if not devices:
        raise GoveeError("No cached devices. Run 'govee scan' first.")
    mapping = load_map()
    if not mapping:
        return devices

    addr_to_dev = {d["address"]: d for d in devices}

    if mapping.get("type") == "2d":
        mapped = []
        for entry in mapping["positions"]:
            dev = addr_to_dev.get(entry["address"])
            if dev:
                d = dict(dev)
                d["x"] = entry["x"]
                d["y"] = entry["y"]
                mapped.append(d)
        mapped_addrs = {e["address"] for e in mapping["positions"]}
        for d in devices:
            if d["address"] not in mapped_addrs:
                mapped.append(d)
        return mapped
    else:
        ordering = mapping.get("order", [])
        mapped = []
        for addr in ordering:
            if addr in addr_to_dev:
                mapped.append(addr_to_dev[addr])
        mapped_addrs = set(ordering)
        for d in devices:
            if d["address"] not in mapped_addrs:
                mapped.append(d)
        return mapped


def get_2d_positions(devices):
    """Extract (x, y) positions from devices if available. Returns list or None."""
    if all("x" in d and "y" in d for d in devices):
        return [(d["x"], d["y"]) for d in devices]
    return None


def get_all_devices():
    """Load devices in mapped order."""
    return get_mapped_devices()


def format_device(dev):
    """Human-readable device string."""
    model = dev.get("model", "")
    name = dev.get("name", "Unknown")
    addr = dev.get("address", "")
    if model:
        return f"{model} ({name}) [{addr}]"
    return f"{name} [{addr}]"


# ─── BLE Operations ──────────────────────────────────────────────────────────

async def scan_devices(timeout=SCAN_TIMEOUT):
    """Scan for Govee H6010 BLE devices. Returns list of device dicts."""
    discovered = await BleakScanner.discover(timeout=timeout)
    devices = []
    seen = set()
    for d in discovered:
        name = d.name or ""
        name_lower = name.lower()
        if not any(prefix in name_lower for prefix in ["govee", "ihoment"]):
            continue
        if "h6010" not in name_lower:
            continue
        if d.address in seen:
            continue
        seen.add(d.address)
        model = ""
        parts = name.replace("-", "_").split("_")
        for p in parts:
            if p.startswith("H") and len(p) >= 4 and p[1:].isalnum():
                model = p
                break
        devices.append({
            "address": d.address,
            "name": name,
            "model": model,
        })
    if devices:
        save_cache(devices)
    return devices


async def send_command(address, packet, wait_response=False):
    """Connect to a device and send a BLE command."""
    async with BleakClient(address, timeout=10) as client:
        await client.write_gatt_char(GOVEE_WRITE_UUID, KEEPALIVE_BYTES, response=False)
        await asyncio.sleep(0.05)
        if wait_response:
            response_data = asyncio.get_event_loop().create_future()

            def on_notify(_sender, data):
                if not response_data.done():
                    response_data.set_result(data)

            await client.start_notify(GOVEE_READ_UUID, on_notify)
            await client.write_gatt_char(GOVEE_WRITE_UUID, packet, response=False)
            try:
                result = await asyncio.wait_for(response_data, timeout=3)
                await client.stop_notify(GOVEE_READ_UUID)
                return result
            except asyncio.TimeoutError:
                await client.stop_notify(GOVEE_READ_UUID)
                return None
        else:
            await client.write_gatt_char(GOVEE_WRITE_UUID, packet, response=False)
            await asyncio.sleep(0.05)
            return True


async def send_to_all(devices, packet_fn):
    """Send commands to all devices with controlled concurrency.

    packet_fn: either a bytes packet (same for all) or callable(i, dev) -> bytes.
    Returns list of (dev, success, error) tuples.
    """
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _send(dev, pkt):
        async with sem:
            try:
                await send_command(dev["address"], pkt)
                return (dev, True, None)
            except Exception as e:
                return (dev, False, str(e))

    if callable(packet_fn):
        tasks = [_send(d, packet_fn(i, d)) for i, d in enumerate(devices)]
    else:
        tasks = [_send(d, packet_fn) for d in devices]

    return await asyncio.gather(*tasks)


async def send_to_all_simple(devices, packet):
    """Send same packet to all devices. Returns (ok_count, fail_count, failures)."""
    results = await send_to_all(devices, packet)
    ok = sum(1 for _, success, _ in results if success)
    failures = [(dev, err) for dev, success, err in results if not success]
    return ok, len(failures), failures


# ─── Device Resolution ────────────────────────────────────────────────────────

async def resolve_device(arg=None):
    """Resolve a CLI arg to a device dict. Auto-scans if cache is empty."""
    devices = load_cache()
    if not devices:
        devices = await scan_devices()
    if not devices:
        raise GoveeError(
            "No Govee devices found.\n"
            "  - Make sure Bluetooth is enabled on this PC\n"
            "  - Move closer to the bulbs\n"
            "  - Try: govee scan"
        )
    if arg is None:
        if len(devices) == 1:
            return devices[0]
        raise GoveeError(
            "Multiple devices found. Specify one by name, model, MAC, or index."
        )
    # Try numeric index
    try:
        idx = int(arg) - 1
        if 0 <= idx < len(devices):
            return devices[idx]
    except ValueError:
        pass
    # Match by address, model, or name
    needle = arg.lower()
    for d in devices:
        if d["address"].lower() == needle:
            return d
        if d["model"].lower() == needle:
            return d
        if needle in d["name"].lower():
            return d
        if needle in d["address"].lower():
            return d
    raise GoveeError(f'Device "{arg}" not found. Run "govee scan" to refresh.')


# ─── Connection Pool ──────────────────────────────────────────────────────────

class ConnectionPool:
    """Persistent BLE connections for fast repeated writes."""

    def __init__(self, devices, max_concurrent=MAX_CONCURRENT):
        self.devices = devices
        self.clients = {}
        self.sem = asyncio.Semaphore(max_concurrent)
        self.failed = set()
        self._reconnecting = set()
        self._ka_task = None

    async def connect_all(self):
        """Open connections to all devices with retries."""
        sem = asyncio.Semaphore(MAX_CONCURRENT)

        async def _connect(dev):
            addr = dev["address"]
            async with sem:
                for attempt in range(3):
                    try:
                        client = BleakClient(addr, timeout=10)
                        await client.connect()
                        await client.write_gatt_char(GOVEE_WRITE_UUID, KEEPALIVE_BYTES, response=False)
                        await asyncio.sleep(0.05)
                        await client.write_gatt_char(GOVEE_WRITE_UUID, power_packet(True), response=False)
                        self.clients[addr] = client
                        return True
                    except Exception:
                        if attempt < 2:
                            await asyncio.sleep(1)
                self.failed.add(addr)
                return False

        tasks = [_connect(d) for d in self.devices]
        results = await asyncio.gather(*tasks)
        return sum(1 for r in results if r), len(self.devices)

    async def disconnect_all(self):
        for addr, client in self.clients.items():
            try:
                await client.disconnect()
            except Exception:
                pass
        self.clients.clear()

    async def _safe_write(self, addr, client, packet):
        """Write with keepalive and reconnect on failure."""
        try:
            if not client.is_connected:
                await self._reconnect(addr)
                client = self.clients.get(addr)
                if not client:
                    return
            await client.write_gatt_char(GOVEE_WRITE_UUID, KEEPALIVE_BYTES, response=False)
            await client.write_gatt_char(GOVEE_WRITE_UUID, packet, response=False)
        except Exception:
            try:
                await self._reconnect(addr)
                client = self.clients.get(addr)
                if client:
                    await client.write_gatt_char(GOVEE_WRITE_UUID, packet, response=False)
            except Exception:
                pass

    async def write_all(self, packet):
        """Write same packet to all connected devices."""
        tasks = [self._safe_write(a, c, packet) for a, c in list(self.clients.items())]
        await asyncio.gather(*tasks)

    async def write_each(self, packet_fn):
        """Write per-device packet. packet_fn(index, address) -> bytes."""
        async def _write(i, addr, client):
            pkt = packet_fn(i, addr)
            await self._safe_write(addr, client, pkt)

        tasks = [_write(i, a, c) for i, (a, c) in enumerate(self.clients.items())]
        await asyncio.gather(*tasks)

    def _mark_stale(self, addr):
        if addr not in self._reconnecting:
            self._reconnecting.add(addr)

    async def _reconnect(self, addr):
        self._reconnecting.discard(addr)
        try:
            old = self.clients.get(addr)
            if old:
                try:
                    await old.disconnect()
                except Exception:
                    pass
            client = BleakClient(addr, timeout=5)
            await client.connect()
            await client.write_gatt_char(GOVEE_WRITE_UUID, KEEPALIVE_BYTES, response=False)
            self.clients[addr] = client
        except Exception:
            self.clients.pop(addr, None)

    async def keepalive(self):
        """Send keepalive to all connected devices."""
        async def _ka(addr, client):
            try:
                if client.is_connected:
                    await client.write_gatt_char(GOVEE_WRITE_UUID, KEEPALIVE_BYTES, response=False)
                else:
                    await self._reconnect(addr)
            except Exception:
                self._mark_stale(addr)

        tasks = [_ka(a, c) for a, c in list(self.clients.items())]
        await asyncio.gather(*tasks)

    async def _keepalive_loop(self):
        while True:
            await asyncio.sleep(0.5)
            await self.keepalive()

    def start_keepalive(self):
        if not self._ka_task:
            self._ka_task = asyncio.create_task(self._keepalive_loop())

    def stop_keepalive(self):
        if self._ka_task:
            self._ka_task.cancel()
            self._ka_task = None

    async def __aenter__(self):
        await self.connect_all()
        self.start_keepalive()
        return self

    async def __aexit__(self, *exc):
        self.stop_keepalive()
        await self.disconnect_all()
