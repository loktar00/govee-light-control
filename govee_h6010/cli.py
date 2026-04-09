"""Govee H6010 CLI entry point with --json mode."""

import asyncio
import json
import sys
from pathlib import Path

from bleak import BleakClient

from .protocol import (
    scan_devices,
    send_command,
    send_to_all_simple,
    color_packet,
    brightness_packet,
    power_packet,
    white_packet,
    kelvin_to_warmth,
    make_packet,
    parse_hex_color,
    load_cache,
    save_cache,
    load_map,
    save_map,
    parse_grid,
    get_mapped_devices,
    get_all_devices,
    resolve_device,
    format_device,
    GoveeError,
    CACHE_PATH,
    MAP_PATH,
    GOVEE_WRITE_UUID,
    KEEPALIVE_BYTES,
    MAX_CONCURRENT,
    ConnectionPool,
)
from .effects import cmd_fx, EFFECTS

# ─── Global State ────────────────────────────────────────────────────────────

json_mode = False

# ─── JSON Helpers ────────────────────────────────────────────────────────────


def json_output(data):
    """Print JSON object to stdout and exit."""
    print(json.dumps(data))
    sys.exit(0)


def json_error(msg):
    """Print error JSON to stdout and exit."""
    print(json.dumps({"ok": False, "error": msg}))
    sys.exit(1)


# ─── USAGE ───────────────────────────────────────────────────────────────────

USAGE = f"""Govee H6010 BLE Control CLI — no internet required

Commands:
  govee scan                          Discover nearby Govee devices
  govee list                          Show cached devices (no scan)
  govee serve [--port 8766]           Start REST API server (persistent BLE)
  govee on [device]                   Turn on
  govee off [device]                  Turn off
  govee brightness <1-100> [device]   Set brightness
  govee color <hex> [device]          Set color (#ff0000)
  govee white [2700-6500] [device]    White LED (default 4000K)
  govee temp <2700-6500> [device]     Alias for white
  govee status [device]               Query device status

Batch:
  govee all on|off|brightness|color|white|temp [value]

Device mapping (for positional effects):
  govee identify                      Flash each bulb one at a time
  govee map                           Show current mapping
  govee map <order>                   Set linear map, e.g.: map 3,1,4,2,5
  govee map --grid <file>             Set 2D map from ASCII grid file

Effects (all devices, Ctrl+C to stop):
  govee fx spectrum                   Shift through the color spectrum
  govee fx wave                       Rainbow wave across devices
  govee fx breathe [#hex]             Pulse brightness (optional color)
  govee fx party                      Random colors, fast
  govee fx candle                     Warm flickering candlelight
  govee fx sunrise [minutes]          Deep red to warm white (default 5 min)
  govee fx ripple [#hex]              Ripple radiates outward (2D-aware)
  govee fx chase                      Lit bulb chases nearest-neighbor path (2D-aware)
  govee fx wipe                       White/black fill top to bottom (2D, uses white LEDs)
  govee fx rain [#hex]                Color drops fall top to bottom (2D required)

Effect options:
  --speed <multiplier>                Speed up/slow down (default 1.0)
  --duration <seconds>                Auto-stop after N seconds
  --from <device>                     Origin device for ripple/chase

Global options:
  --json                              Output as single JSON object (no human text)

Available effects: {', '.join(EFFECTS)}

Grid file example (use device numbers from identify):
  6.7.....
  ....3.12
  .10.....
  ....9.8.
  2..4....
  ....1.11
  ........
  .....5..

Device: name, model, MAC address, or index number.
Cache: {CACHE_PATH}
Map:   {MAP_PATH}
"""


# ─── Command Implementations ────────────────────────────────────────────────


def die(msg):
    """Print error and exit (or JSON error in json_mode)."""
    if json_mode:
        json_error(msg)
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


async def cmd_scan():
    if not json_mode:
        print("Scanning for Govee devices...")
    devices = await scan_devices()
    if json_mode:
        json_output({"ok": True, "devices": devices, "count": len(devices)})
    if not devices:
        print(
            "No Govee devices found.\n"
            "  - Make sure Bluetooth is enabled\n"
            "  - Ensure you're within range of the bulbs"
        )
        return
    print(f"\nFound {len(devices)} device(s):\n")
    for i, d in enumerate(devices, 1):
        print(f"  {i}. {format_device(d)}")
    print(f"\nDevices cached to {CACHE_PATH}")


async def cmd_list():
    """Show cached devices without scanning."""
    devices = load_cache()
    if json_mode:
        json_output({"ok": True, "devices": devices, "count": len(devices)})
    if not devices:
        print("No cached devices. Run 'govee scan' first.")
        return
    print(f"Cached devices ({len(devices)}):\n")
    for i, d in enumerate(devices, 1):
        print(f"  {i}. {format_device(d)}")
    print(f"\nCache: {CACHE_PATH}")


async def cmd_on(device_arg=None):
    dev = await resolve_device(device_arg)
    await send_command(dev["address"], power_packet(True))
    if json_mode:
        json_output({"ok": True, "device": dev["address"], "action": "on"})
    print(f"{format_device(dev)} turned ON")


async def cmd_off(device_arg=None):
    dev = await resolve_device(device_arg)
    await send_command(dev["address"], power_packet(False))
    if json_mode:
        json_output({"ok": True, "device": dev["address"], "action": "off"})
    print(f"{format_device(dev)} turned OFF")


async def cmd_brightness(value_str, device_arg=None):
    try:
        val = int(value_str)
    except ValueError:
        die("Brightness must be a number 1-100")
    if val < 1 or val > 100:
        die("Brightness must be 1-100")
    dev = await resolve_device(device_arg)
    await send_command(dev["address"], brightness_packet(val))
    if json_mode:
        json_output({"ok": True, "device": dev["address"], "brightness": val})
    print(f"{format_device(dev)} brightness set to {val}%")


async def cmd_color(hex_str, device_arg=None):
    r, g, b = parse_hex_color(hex_str)
    dev = await resolve_device(device_arg)
    await send_command(dev["address"], color_packet(r, g, b))
    color_hex = f"#{hex_str.lstrip('#')}"
    if json_mode:
        json_output({"ok": True, "device": dev["address"], "color": color_hex})
    print(f"{format_device(dev)} color set to {color_hex}")


async def cmd_white(kelvin_str=None, device_arg=None):
    if kelvin_str is None:
        kelvin_str = "4000"
    try:
        val = int(kelvin_str)
    except ValueError:
        die("Temperature must be a number 2700-6500")
    if val < 2700 or val > 6500:
        die("Temperature must be 2700-6500")
    dev = await resolve_device(device_arg)
    warmth = kelvin_to_warmth(val)
    await send_command(dev["address"], white_packet(warmth))
    if json_mode:
        json_output({"ok": True, "device": dev["address"], "temperature": val})
    print(f"{format_device(dev)} white LED set to {val}K")


async def cmd_temp(kelvin_str, device_arg=None):
    await cmd_white(kelvin_str, device_arg)


async def cmd_status(device_arg=None):
    dev = await resolve_device(device_arg)
    packet = make_packet(0xAA, 0x08)
    resp = await send_command(dev["address"], packet, wait_response=True)

    if json_mode:
        result = {"ok": True, "device": dev["address"]}
        if resp:
            data = list(resp)
            if len(data) >= 5:
                result["power"] = "on" if data[2] == 1 else "off"
                result["brightness"] = data[3]
            if len(data) >= 8:
                r, g, b = data[5], data[6], data[7]
                result["color"] = f"#{r:02x}{g:02x}{b:02x}"
                result["rgb"] = [r, g, b]
        else:
            result["status"] = "unavailable"
        json_output(result)

    print(f"Device: {format_device(dev)}")
    if resp:
        data = list(resp)
        if len(data) >= 5:
            print(f"  Power:      {'ON' if data[2] == 1 else 'OFF'}")
            print(f"  Brightness: {data[3]}%")
        if len(data) >= 8:
            r, g, b = data[5], data[6], data[7]
            print(f"  Color:      #{r:02x}{g:02x}{b:02x} (rgb {r}, {g}, {b})")
    else:
        print("  Could not read status (device may not support status query)")


async def cmd_identify():
    """Flash each device one at a time so user can note physical positions."""
    devices = load_cache()
    if not devices:
        devices = await scan_devices()
    if not devices:
        die("No devices found.")

    print(f"Identifying {len(devices)} devices. Each will flash WHITE one at a time.\n")
    print("Write down the position number you see for each bulb.\n")

    results = []

    for i, dev in enumerate(devices):
        addr = dev["address"]
        suffix = dev.get("name", addr).split("_")[-1]
        try:
            async with BleakClient(addr, timeout=10) as client:
                await client.write_gatt_char(GOVEE_WRITE_UUID, KEEPALIVE_BYTES, response=False)
                await asyncio.sleep(0.05)
                # Turn on, bright white
                await client.write_gatt_char(GOVEE_WRITE_UUID, power_packet(True), response=False)
                await asyncio.sleep(0.05)
                await client.write_gatt_char(GOVEE_WRITE_UUID, brightness_packet(100), response=False)
                await asyncio.sleep(0.05)
                await client.write_gatt_char(GOVEE_WRITE_UUID, color_packet(255, 255, 255), response=False)

                print(f"  [{i+1:2d}] {suffix}  ({addr})  <-- THIS ONE IS LIT")
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("        Press Enter for next bulb...")
                )

                # Turn off
                await client.write_gatt_char(GOVEE_WRITE_UUID, power_packet(False), response=False)
                await asyncio.sleep(0.1)

            results.append((i + 1, suffix, addr, True))
        except Exception as e:
            print(f"  [{i+1:2d}] {suffix}  ({addr})  <-- FAILED: {e}")
            results.append((i + 1, suffix, addr, False))

    print(f"\nDone. Now set your map with:")
    print(f"  govee map <positions>")
    print(f"\nExample: if bulb [1] is your 3rd position, [2] is 1st, [3] is 2nd:")
    print(f"  govee map 2,3,1")


def cmd_map(args):
    """Save or show the physical device mapping."""
    devices = load_cache()
    if not devices:
        die("No cached devices. Run 'govee scan' first.")

    if not args:
        # Show current map
        mapping = load_map()
        if not mapping:
            if json_mode:
                json_output({"ok": True, "mapped": False, "devices": devices})
            print("No map set. Run 'govee identify' then 'govee map'")
            print(f"\nCurrent device order (unmapped):")
            for i, d in enumerate(devices, 1):
                suffix = d["name"].split("_")[-1]
                print(f"  {i:2d}. {suffix}  ({d['address']})")
            return

        addr_to_dev = {d["address"]: d for d in devices}

        if mapping.get("type") == "2d":
            if json_mode:
                json_output({"ok": True, "type": "2d", "positions": mapping["positions"]})
            print(f"2D device map ({len(mapping['positions'])} devices):\n")
            for entry in mapping["positions"]:
                dev = addr_to_dev.get(entry["address"], {})
                suffix = dev.get("name", "?").split("_")[-1]
                print(f"  #{entry['index']:2d} {suffix}  x={entry['x']:.0f} y={entry['y']:.0f}  ({entry['address']})")
            print(f"\nMap file: {MAP_PATH}")
        else:
            ordering = mapping.get("order", [])
            if json_mode:
                json_output({"ok": True, "type": "1d", "order": ordering})
            print(f"Linear device map ({len(ordering)} positions):\n")
            for pos, addr in enumerate(ordering, 1):
                dev = addr_to_dev.get(addr)
                if dev:
                    suffix = dev["name"].split("_")[-1]
                    print(f"  Pos {pos:2d}: {suffix}  ({addr})")
            print(f"\nMap file: {MAP_PATH}")
        return

    # Check for --grid flag
    if args[0] == "--grid":
        if len(args) < 2:
            die("Usage: govee map --grid <file>\n"
                "  The file should be an ASCII grid with device numbers (from identify).\n"
                "  Use any non-digit character for empty space.\n\n"
                "  Example grid file:\n"
                "    6.7.....\n"
                "    ....3.12\n"
                "    .10.....\n"
                "    ....9.8.\n"
                "    2..4....\n"
                "    ....1.11\n"
                "    ........\n"
                "    .....5..")
        grid_file = Path(args[1])
        if not grid_file.exists():
            die(f"File not found: {grid_file}")
        grid_text = grid_file.read_text()
        positions = parse_grid(grid_text)

        if not positions:
            die("No device numbers found in grid file.")

        # Validate indices
        for idx in positions:
            if idx < 1 or idx > len(devices):
                die(f"Device #{idx} in grid is out of range (1-{len(devices)})")

        # Build 2D map
        entries = []
        for idx in sorted(positions.keys()):
            x, y = positions[idx]
            entries.append({
                "index": idx,
                "address": devices[idx - 1]["address"],
                "x": x,
                "y": y,
            })

        map_data = {"type": "2d", "positions": entries}
        save_map(map_data)

        if json_mode:
            json_output({"ok": True, "type": "2d", "positions": entries})

        addr_to_dev = {d["address"]: d for d in devices}
        print(f"2D map saved ({len(entries)} devices):\n")
        for entry in entries:
            dev = addr_to_dev[entry["address"]]
            suffix = dev["name"].split("_")[-1]
            print(f"  #{entry['index']:2d} {suffix}  x={entry['x']:.0f} y={entry['y']:.0f}")

        print(f"\nSaved to {MAP_PATH}")
        return

    # Linear ordering: comma-separated indices
    order_str = args[0]
    try:
        indices = [int(x.strip()) for x in order_str.split(",")]
    except ValueError:
        die("Map must be comma-separated numbers, e.g.: govee map 2,3,1,5,4")

    for idx in indices:
        if idx < 1 or idx > len(devices):
            die(f"Index {idx} out of range (1-{len(devices)})")

    ordering = [devices[i - 1]["address"] for i in indices]
    save_map({"type": "1d", "order": ordering})

    if json_mode:
        json_output({"ok": True, "type": "1d", "order": ordering})

    addr_to_dev = {d["address"]: d for d in devices}
    print(f"Map saved ({len(ordering)} positions):\n")
    for pos, addr in enumerate(ordering, 1):
        dev = addr_to_dev[addr]
        suffix = dev["name"].split("_")[-1]
        print(f"  Pos {pos:2d}: {suffix}  ({addr})")
    print(f"\nSaved to {MAP_PATH}")


async def cmd_all(action, extra_arg=None):
    """Run a command on ALL cached/scanned devices in parallel."""
    devices = load_cache()
    if not devices:
        devices = await scan_devices()
    if not devices:
        die("No devices found.")

    if action == "on":
        pkt = power_packet(True)
    elif action == "off":
        pkt = power_packet(False)
    elif action == "brightness":
        pkt = brightness_packet(int(extra_arg))
    elif action == "color":
        r, g, b = parse_hex_color(extra_arg)
        pkt = color_packet(r, g, b)
    elif action in ("temp", "white"):
        val = int(extra_arg) if extra_arg else 4000
        warmth = kelvin_to_warmth(val)
        pkt = white_packet(warmth)
    else:
        die(f"Unknown action: {action}")

    if not json_mode:
        print(f"Sending '{action}' to {len(devices)} device(s)...")
    ok, fail, failures = await send_to_all_simple(devices, pkt)
    if json_mode:
        json_output({"ok": True, "action": action, "succeeded": ok, "failed": fail})
    if fail:
        for dev, err in failures:
            print(f"  FAIL {format_device(dev)}: {err}")
    print(f"Done. {ok} OK, {fail} failed.")


async def cmd_fx_wrapper(fx_name, args):
    """Run an effect on all devices using persistent connections."""
    devices = get_all_devices()

    # Parse common options
    speed = 1.0
    duration = None
    origin_arg = None
    remaining = list(args)

    i = 0
    while i < len(remaining):
        if remaining[i] == "--speed" and i + 1 < len(remaining):
            speed = float(remaining[i + 1])
            remaining.pop(i)
            remaining.pop(i)
        elif remaining[i] == "--duration" and i + 1 < len(remaining):
            duration = float(remaining[i + 1])
            remaining.pop(i)
            remaining.pop(i)
        elif remaining[i] == "--from" and i + 1 < len(remaining):
            origin_arg = remaining[i + 1]
            remaining.pop(i)
            remaining.pop(i)
        else:
            i += 1

    extra = remaining[0] if remaining else None

    # Resolve --from to a device index
    origin = None
    if origin_arg is not None:
        needle = origin_arg.lower()
        for idx, d in enumerate(devices):
            if d["address"].lower() == needle or \
               needle in d.get("name", "").lower() or \
               d.get("name", "").split("_")[-1].lower() == needle or \
               d["address"].lower().endswith(needle):
                origin = idx
                break
        if origin is None:
            try:
                origin = int(origin_arg) - 1
            except ValueError:
                die(f"Could not find device matching '{origin_arg}'")

    # on_connect callback for human mode status
    def on_connect(dev, success):
        if not json_mode:
            if success:
                print(f"  + {format_device(dev)}")
            else:
                print(f"  x {format_device(dev)} (3 attempts failed)")

    def on_pool_ready(connected, total):
        if not json_mode:
            print(f"\n  {connected}/{total} connected\n")

    if not json_mode:
        print(f"Connecting to {len(devices)} devices (3 retries each)...")

    await cmd_fx(fx_name, args, quiet=json_mode, on_connect=on_pool_ready)

    if json_mode:
        json_output({"ok": True, "effect": fx_name, "duration": duration})

    if not json_mode:
        print("\nConnections closed.")


# ─── CLI Main ────────────────────────────────────────────────────────────────


async def async_main():
    global json_mode

    args = sys.argv[1:]

    # Extract --json flag from anywhere in args
    if "--json" in args:
        json_mode = True
        args = [a for a in args if a != "--json"]

    if not args:
        if json_mode:
            json_error("No command specified")
        print(USAGE)
        return

    cmd = args[0].lower()

    if cmd in ("help", "--help", "-h"):
        if json_mode:
            json_output({"ok": True, "commands": [
                "scan", "list", "on", "off", "brightness", "color",
                "white", "temp", "status", "identify", "map", "all", "fx", "help",
            ]})
        print(USAGE)
        return

    if cmd == "scan":
        await cmd_scan()

    elif cmd == "list":
        await cmd_list()

    elif cmd == "identify":
        await cmd_identify()

    elif cmd == "map":
        cmd_map(args[1:])

    elif cmd == "fx":
        if len(args) < 2:
            die("Usage: govee fx <effect> [options]\n"
                f"  Effects: {', '.join(EFFECTS)}")
        await cmd_fx_wrapper(args[1].lower(), args[2:])

    elif cmd == "all":
        if len(args) < 2:
            die("Usage: govee all <on|off|brightness|color|temp> [value]")
        action = args[1].lower()
        extra = args[2] if len(args) > 2 else None
        if action in ("brightness", "color", "temp") and extra is None:
            die(f"Usage: govee all {action} <value>")
        await cmd_all(action, extra)

    elif cmd == "on":
        await cmd_on(args[1] if len(args) > 1 else None)

    elif cmd == "off":
        await cmd_off(args[1] if len(args) > 1 else None)

    elif cmd == "brightness":
        if len(args) < 2:
            die("Usage: govee brightness <1-100> [device]")
        await cmd_brightness(args[1], args[2] if len(args) > 2 else None)

    elif cmd == "color":
        if len(args) < 2:
            die("Usage: govee color <hex> [device]")
        await cmd_color(args[1], args[2] if len(args) > 2 else None)

    elif cmd == "white":
        await cmd_white(args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None)

    elif cmd == "temp":
        await cmd_white(args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None)

    elif cmd == "status":
        await cmd_status(args[1] if len(args) > 1 else None)

    elif cmd == "serve":
        from .server import main as serve_main
        port_arg = None
        for i, a in enumerate(args[1:], 1):
            if a == "--port" and i < len(args) - 1:
                port_arg = args[i + 1]
        if port_arg:
            sys.argv = ["govee-server", "--port", port_arg]
        else:
            sys.argv = ["govee-server"]
        serve_main()

    else:
        die(f"Unknown command: {cmd}\nRun 'govee help' for usage.")


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        if json_mode:
            pass
        else:
            print("\nStopped.")
    except GoveeError as e:
        if json_mode:
            json_error(str(e))
        else:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
