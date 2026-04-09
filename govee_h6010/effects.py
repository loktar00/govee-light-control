"""Govee H6010 lighting effects — ported from govee.py with quiet mode support."""

import asyncio
import math
import random
import time
from collections import defaultdict

from .protocol import (
    color_packet,
    brightness_packet,
    power_packet,
    white_packet,
    hsv_to_rgb,
    parse_hex_color,
    get_2d_positions,
    get_all_devices,
    GoveeError,
    ConnectionPool,
    MAX_CONCURRENT,
    GOVEE_WRITE_UUID,
    KEEPALIVE_BYTES,
    format_device,
)

# ─── Effect Registry ─────────────────────────────────────────────────────────

EFFECTS = {
    "spectrum": "Shift all lights through the color spectrum in unison",
    "wave": "Rainbow wave flowing across devices",
    "breathe": "Pulse brightness up and down (optional color)",
    "party": "Random colors on each device, fast switching",
    "candle": "Warm flickering candlelight",
    "sunrise": "Gradual deep red to warm white",
    "ripple": "Color flood fill radiating outward (2D-aware)",
    "chase": "Single lit bulb chases in snake pattern (2D-aware)",
    "rain": "Color drops fall top to bottom (2D required)",
    "wipe": "White/black fill top to bottom (2D, uses white LEDs)",
}


# ─── Effect Functions ─────────────────────────────────────────────────────────

async def fx_spectrum(pool, speed=1.0, duration=None, quiet=False):
    """Slowly shift all lights through the color spectrum in unison."""
    step_time = 0.3 / speed
    hue = 0.0
    start = time.time()

    while True:
        if duration and (time.time() - start) >= duration:
            break
        r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
        await pool.write_all(color_packet(r, g, b))
        hue = (hue + 0.01) % 1.0
        if not quiet:
            print(f"\r  hue: {hue:.2f}  color: #{r:02x}{g:02x}{b:02x}", end="", flush=True)
        await asyncio.sleep(step_time)


async def fx_wave(pool, speed=1.0, duration=None, quiet=False):
    """Rainbow wave — each device is offset in hue, creating a flowing wave."""
    n = max(len(pool.clients), 1)
    step_time = 0.3 / speed
    offset = 0.0
    start = time.time()

    while True:
        if duration and (time.time() - start) >= duration:
            break
        _offset = offset  # capture for closure

        def pkt_fn(i, _addr, _o=_offset):
            h = (_o + i / n) % 1.0
            r, g, b = hsv_to_rgb(h, 1.0, 1.0)
            return color_packet(r, g, b)

        await pool.write_each(pkt_fn)
        offset = (offset + 0.02) % 1.0
        if not quiet:
            print(f"\r  offset: {offset:.2f}", end="", flush=True)
        await asyncio.sleep(step_time)


async def fx_breathe(pool, speed=1.0, duration=None, hex_color=None, quiet=False):
    """Pulse brightness up and down like breathing."""
    if hex_color:
        r, g, b = parse_hex_color(hex_color)
        label = f"#{hex_color.lstrip('#')}"
    else:
        r, g, b = 255, 255, 255
        label = "white"

    if not quiet:
        print(f"  color: {label}")
    step_time = 0.15 / speed
    t = 0.0
    start = time.time()

    await pool.write_all(color_packet(r, g, b))
    await asyncio.sleep(0.2)

    while True:
        if duration and (time.time() - start) >= duration:
            break
        brightness = int(5 + 95 * (0.5 + 0.5 * math.sin(t)))
        await pool.write_all(brightness_packet(brightness))
        if not quiet:
            print(f"\r  brightness: {brightness:3d}%", end="", flush=True)
        t += 0.15
        await asyncio.sleep(step_time)


async def fx_party(pool, speed=1.0, duration=None, quiet=False):
    """Random colors on each device, fast switching."""
    step_time = 0.4 / speed
    start = time.time()

    while True:
        if duration and (time.time() - start) >= duration:
            break

        def pkt_fn(i, _addr):
            r = random.randint(0, 255)
            g = random.randint(0, 255)
            b = random.randint(0, 255)
            return color_packet(r, g, b)

        await pool.write_each(pkt_fn)
        await asyncio.sleep(step_time)


async def fx_candle(pool, speed=1.0, duration=None, quiet=False):
    """Warm flickering candlelight effect."""
    start = time.time()

    while True:
        if duration and (time.time() - start) >= duration:
            break

        def pkt_fn(i, _addr):
            r = random.randint(200, 255)
            g = random.randint(80, 140)
            b = random.randint(0, 20)
            return color_packet(r, g, b)

        await pool.write_each(pkt_fn)
        await asyncio.sleep(random.uniform(0.15, 0.5) / speed)


async def fx_sunrise(pool, duration_minutes=5, quiet=False):
    """Gradually shift from deep red to warm white over time, simulating sunrise."""
    total_steps = 60
    step_time = (duration_minutes * 60) / total_steps

    for step in range(total_steps + 1):
        t = step / total_steps

        if t < 0.3:
            p = t / 0.3
            r, g, b = 180, int(40 * p), 0
        elif t < 0.6:
            p = (t - 0.3) / 0.3
            r, g, b = 220, int(40 + 120 * p), int(20 * p)
        else:
            p = (t - 0.6) / 0.4
            r, g, b = int(220 + 35 * p), int(160 + 80 * p), int(20 + 180 * p)

        brightness = max(1, int(1 + 99 * t))

        await pool.write_all(color_packet(r, g, b))
        await asyncio.sleep(0.2)
        await pool.write_all(brightness_packet(brightness))

        elapsed = step * step_time
        if not quiet:
            print(f"\r  {elapsed:.0f}s / {duration_minutes * 60:.0f}s  brightness: {brightness}%  "
                  f"color: #{r:02x}{g:02x}{b:02x}", end="", flush=True)

        if step < total_steps:
            await asyncio.sleep(max(0, step_time - 0.2))

    if not quiet:
        print("\n  Sunrise complete.")


async def fx_ripple(pool, devices, speed=1.0, duration=None, hex_color=None, origin=None, quiet=False):
    """Color flood fill — each wave ripples outward from an origin and fills, then the next color takes over."""
    n = max(len(pool.clients), 1)
    positions = get_2d_positions(devices)

    step_time = 0.2 / speed
    start = time.time()

    if positions:
        if origin is not None and 0 <= origin < n:
            cx, cy = positions[origin]
            if not quiet:
                print(f"  2D flood fill from device #{origin+1} ({cx:.0f},{cy:.0f})")
        else:
            cx = sum(x for x, y in positions) / n
            cy = sum(y for x, y in positions) / n
            if not quiet:
                print(f"  2D flood fill from center ({cx:.0f},{cy:.0f})")
        dists = [math.sqrt((x - cx) ** 2 + (y - cy) ** 2) for x, y in positions]
        max_dist = max(dists) if dists else 1.0
    else:
        dists = list(range(n))
        max_dist = n - 1
        if not quiet:
            print(f"  1D flood fill across {n} devices")

    hue = 0.0

    # Start blacked out
    await pool.write_all(color_packet(0, 0, 0))
    await asyncio.sleep(0.5)

    while True:
        if duration and (time.time() - start) >= duration:
            break

        # Current wave color
        cr, cg, cb = hsv_to_rgb(hue, 1.0, 1.0)

        # Phase 1: Fill with color (from black)
        fill_steps = int(max_dist / 0.5) + 1
        for step in range(fill_steps + 1):
            if duration and (time.time() - start) >= duration:
                break
            radius = (step / fill_steps) * max_dist
            _radius = radius
            _cr, _cg, _cb = cr, cg, cb

            def pkt_color(i, _addr, _rad=_radius, _r=_cr, _g=_cg, _b=_cb):
                d = dists[i]
                if d <= _rad:
                    return color_packet(_r, _g, _b)
                return color_packet(0, 0, 0)

            await pool.write_each(pkt_color)
            if not quiet:
                print(f"\r  #{cr:02x}{cg:02x}{cb:02x}  fill: {min(100, int(radius / max_dist * 100)):3d}%", end="", flush=True)
            await asyncio.sleep(step_time)

        # Hold the color
        await asyncio.sleep(3.0 / speed)

        if duration and (time.time() - start) >= duration:
            break

        # Phase 2: Fill with black (wipe out the color)
        for step in range(fill_steps + 1):
            if duration and (time.time() - start) >= duration:
                break
            radius = (step / fill_steps) * max_dist
            _radius = radius
            _cr, _cg, _cb = cr, cg, cb

            def pkt_black(i, _addr, _rad=_radius, _r=_cr, _g=_cg, _b=_cb):
                d = dists[i]
                if d <= _rad:
                    return color_packet(0, 0, 0)
                return color_packet(_r, _g, _b)

            await pool.write_each(pkt_black)
            if not quiet:
                print(f"\r  blackout   fill: {min(100, int(radius / max_dist * 100)):3d}%", end="", flush=True)
            await asyncio.sleep(step_time)

        # Brief pause in darkness
        await asyncio.sleep(1.0 / speed)

        # Next color
        hue = (hue + 0.15) % 1.0


async def fx_chase(pool, devices, speed=1.0, duration=None, origin=None, quiet=False):
    """Single lit bulb chases in a snake pattern. Changes color each lap."""
    n = max(len(pool.clients), 1)
    positions = get_2d_positions(devices)

    if positions:
        # Group devices by row (y value)
        rows = defaultdict(list)
        for i, (x, y) in enumerate(positions):
            rows[y].append((i, x))

        sorted_ys = sorted(rows.keys())

        # Find start row
        start_row_idx = 0
        if origin is not None and 0 <= origin < n:
            start_y = positions[origin][1]
            for ri, y in enumerate(sorted_ys):
                if y == start_y:
                    start_row_idx = ri
                    break

        # Reorder rows starting from origin row, going down then wrapping to top
        ordered_ys = sorted_ys[start_row_idx:] + sorted_ys[:start_row_idx]

        # Build snake path: alternate x+ and x- per row
        # First row goes x+ from the origin device
        path = []
        go_right = True

        for y in ordered_ys:
            row_devices = rows[y]
            row_devices.sort(key=lambda item: item[1])  # sort by x
            if not go_right:
                row_devices.reverse()

            # If this is the start row and we have an origin, start from that device
            if y == ordered_ys[0] and origin is not None:
                origin_x = positions[origin][0]
                # Split: origin device first, then the rest in direction order
                before = [d for d in row_devices if d[0] == origin]
                after = [d for d in row_devices if d[0] != origin]
                row_devices = before + after

            path.extend([i for i, x in row_devices])
            go_right = not go_right

        suffix_map = {d["address"]: d.get("name", "").split("_")[-1] for d in devices}
        addrs = list(pool.clients.keys())
        path_names = []
        for p in path:
            if p < len(devices):
                path_names.append(devices[p].get("name", "").split("_")[-1])
        if not quiet:
            print(f"  Snake path: {' -> '.join(path_names)}")
    else:
        path = list(range(n))

    step_time = 0.4 / speed
    start = time.time()
    step = 0
    hue = 0.0
    r, g, b = hsv_to_rgb(hue, 1.0, 1.0)

    await pool.write_all(color_packet(0, 0, 0))
    await asyncio.sleep(0.2)

    while True:
        if duration and (time.time() - start) >= duration:
            break

        pos_in_lap = step % n
        current = path[pos_in_lap]

        # New color at start of each lap
        if pos_in_lap == 0 and step > 0:
            hue = (hue + 0.15) % 1.0
            r, g, b = hsv_to_rgb(hue, 1.0, 1.0)

        _current = current
        _r, _g, _b = r, g, b

        def pkt_fn(i, _addr, _c=_current, _cr=_r, _cg=_g, _cb=_b):
            if i == _c:
                return color_packet(_cr, _cg, _cb)
            return color_packet(0, 0, 0)

        await pool.write_each(pkt_fn)
        lap = step // n + 1
        if not quiet:
            print(f"\r  lap {lap}  pos {pos_in_lap+1}/{n}  color: #{r:02x}{g:02x}{b:02x}", end="", flush=True)
        step += 1
        await asyncio.sleep(step_time)


async def fx_rain(pool, devices, speed=1.0, duration=None, hex_color=None, quiet=False):
    """Drops of color fall from top to bottom using 2D positions."""
    positions = get_2d_positions(devices)
    if not positions:
        raise GoveeError("Rain effect requires a 2D map. Use: govee map --grid <file>")

    n = len(positions)
    min_y = min(y for _, y in positions)
    max_y = max(y for _, y in positions)
    height = max_y - min_y or 1.0

    step_time = 0.25 / speed
    start = time.time()

    # Active drops: each is a y-position that moves downward
    drops = []
    drop_hue = 0.0

    await pool.write_all(color_packet(0, 0, 0))
    await asyncio.sleep(0.2)

    while True:
        if duration and (time.time() - start) >= duration:
            break

        # Randomly spawn new drops
        if random.random() < 0.3:
            if hex_color:
                dr, dg, db = parse_hex_color(hex_color)
            else:
                dr, dg, db = hsv_to_rgb(drop_hue, 1.0, 1.0)
                drop_hue = (drop_hue + 0.15) % 1.0
            drops.append({"y": min_y - 1, "r": dr, "g": dg, "b": db})

        # Move drops down
        for drop in drops:
            drop["y"] += height * 0.15

        # Remove drops that fell past the bottom
        drops = [d for d in drops if d["y"] <= max_y + height * 0.3]

        _drops = list(drops)

        def pkt_fn(i, _addr, _dr=_drops):
            x, y = positions[i]
            best_intensity = 0.0
            br, bg, bb = 0, 0, 0
            for drop in _dr:
                dist = abs(y - drop["y"])
                intensity = max(0.0, 1.0 - dist / (height * 0.25))
                if intensity > best_intensity:
                    best_intensity = intensity
                    br = int(drop["r"] * intensity)
                    bg = int(drop["g"] * intensity)
                    bb = int(drop["b"] * intensity)
            return color_packet(br, bg, bb)

        await pool.write_each(pkt_fn)
        await asyncio.sleep(step_time)


async def fx_wipe(pool, devices, speed=1.0, duration=None, quiet=False):
    """Fill top to bottom with white, then black, alternating."""
    positions = get_2d_positions(devices)
    if not positions:
        raise GoveeError("Wipe effect requires a 2D map. Use: govee map --grid <file>")

    n = len(positions)
    min_y = min(y for _, y in positions)
    max_y = max(y for _, y in positions)
    height = max_y - min_y or 1.0

    step_time = 0.2 / speed
    start = time.time()
    fill_steps = 15

    # Max brightness, start black
    await pool.write_all(brightness_packet(100))
    await asyncio.sleep(0.2)
    await pool.write_all(color_packet(0, 0, 0))
    await asyncio.sleep(0.3)

    is_white = True

    while True:
        if duration and (time.time() - start) >= duration:
            break

        # For white fill: need to ensure devices are on first
        if is_white:
            await pool.write_all(power_packet(True))
            await asyncio.sleep(0.1)
            await pool.write_all(brightness_packet(100))
            await asyncio.sleep(0.1)

        for step in range(fill_steps + 1):
            if duration and (time.time() - start) >= duration:
                break
            fill_y = min_y + (step / fill_steps) * height

            _fill_y = fill_y
            _white = is_white

            def pkt_fn(i, _addr, _fy=_fill_y, _w=_white):
                _, y = positions[i]
                if y <= _fy:
                    if _w:
                        return white_packet(0x01)
                    else:
                        return power_packet(False)
                else:
                    if _w:
                        return power_packet(False)
                    else:
                        return white_packet(0x01)

            await pool.write_each(pkt_fn)
            if not quiet:
                label = "white" if is_white else "black"
                pct = min(100, int(step / fill_steps * 100))
                print(f"\r  {label}  fill: {pct:3d}%", end="", flush=True)
            await asyncio.sleep(step_time)

        # Hold
        await asyncio.sleep(2.0 / speed)

        is_white = not is_white


# ─── Orchestrator ─────────────────────────────────────────────────────────────

async def cmd_fx(fx_name, args, quiet=False, on_connect=None):
    """Run an effect on all devices using persistent connections.

    Parameters
    ----------
    fx_name : str
        Effect name (must be a key in EFFECTS).
    args : list[str]
        CLI-style arguments: [color], --speed, --duration, --from.
    quiet : bool
        Suppress print output inside effects.
    on_connect : callable or None
        Called as on_connect(connected, total) after the pool connects.
    """
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
            # Try numeric (1-indexed)
            try:
                origin = int(origin_arg) - 1
            except ValueError:
                raise GoveeError(f"Could not find device matching '{origin_arg}'")

    if not quiet:
        print(f"Connecting to {len(devices)} devices (3 retries each)...")

    async with ConnectionPool(devices) as pool:
        if not pool.clients:
            raise GoveeError("Could not connect to any devices.")

        if on_connect is not None:
            on_connect(len(pool.clients), len(devices))

        if not quiet:
            print(f"Running '{fx_name}' (Ctrl+C to stop)\n")

        if fx_name == "spectrum":
            await fx_spectrum(pool, speed, duration, quiet=quiet)
        elif fx_name == "wave":
            await fx_wave(pool, speed, duration, quiet=quiet)
        elif fx_name == "breathe":
            await fx_breathe(pool, speed, duration, extra, quiet=quiet)
        elif fx_name == "party":
            await fx_party(pool, speed, duration, quiet=quiet)
        elif fx_name == "candle":
            await fx_candle(pool, speed, duration, quiet=quiet)
        elif fx_name == "sunrise":
            mins = float(extra) if extra else 5
            await fx_sunrise(pool, mins, quiet=quiet)
        elif fx_name == "ripple":
            await fx_ripple(pool, devices, speed, duration, extra, origin, quiet=quiet)
        elif fx_name == "chase":
            await fx_chase(pool, devices, speed, duration, origin, quiet=quiet)
        elif fx_name == "rain":
            await fx_rain(pool, devices, speed, duration, extra, quiet=quiet)
        elif fx_name == "wipe":
            await fx_wipe(pool, devices, speed, duration, quiet=quiet)
        else:
            raise GoveeError(
                f"Unknown effect: {fx_name}\n"
                "  Available: " + ", ".join(EFFECTS.keys())
            )

    if not quiet:
        print("\nConnections closed.")


# ─── High-Level API ──────────────────────────────────────────────────────────

async def run_effect(name, duration=10, speed=1.0, origin=None, color=None, quiet=True):
    """High-level API for running an effect. Used by MCP server.

    Parameters
    ----------
    name : str
        Effect name (key in EFFECTS).
    duration : float
        How long to run (seconds).
    speed : float
        Speed multiplier (default 1.0).
    origin : str or None
        Device identifier for origin-based effects (address suffix, name, or index).
    color : str or None
        Hex color string for effects that accept one (e.g. "#ff0000").
    quiet : bool
        Suppress print output (default True for API usage).

    Returns
    -------
    dict
        Summary with keys: effect, duration, devices.
    """
    if name not in EFFECTS:
        raise GoveeError(
            f"Unknown effect: {name}. Available: {', '.join(EFFECTS.keys())}"
        )

    devices = get_all_devices()

    # Build args list for cmd_fx-style dispatch
    args = []
    if color:
        args.append(color)
    args.extend(["--speed", str(speed)])
    args.extend(["--duration", str(duration)])
    if origin is not None:
        args.extend(["--from", str(origin)])

    connected_count = 0

    def _on_connect(connected, total):
        nonlocal connected_count
        connected_count = connected

    await cmd_fx(name, args, quiet=quiet, on_connect=_on_connect)

    return {
        "effect": name,
        "duration": duration,
        "devices": connected_count,
    }


async def run_effect_on_pool(pool, devices, name, duration=10, speed=1.0,
                             origin=None, color=None):
    """Run an effect on an already-connected pool (no connection overhead).

    Parameters
    ----------
    pool : PersistentPool or ConnectionPool
        Must have write_all / write_each / clients.
    devices : list[dict]
        Device list (needed for 2D effects).
    name : str
        Effect name.
    duration, speed, origin, color : same as run_effect.
    """
    if name not in EFFECTS:
        raise GoveeError(
            f"Unknown effect: {name}. Available: {', '.join(EFFECTS.keys())}"
        )

    extra = color

    # Resolve origin
    origin_idx = None
    if origin is not None:
        needle = str(origin).lower()
        for idx, d in enumerate(devices):
            if d["address"].lower() == needle or \
               needle in d.get("name", "").lower() or \
               d.get("name", "").split("_")[-1].lower() == needle or \
               d["address"].lower().endswith(needle):
                origin_idx = idx
                break
        if origin_idx is None:
            try:
                origin_idx = int(origin) - 1
            except ValueError:
                raise GoveeError(f"Could not find device matching '{origin}'")

    if name == "spectrum":
        await fx_spectrum(pool, speed, duration, quiet=True)
    elif name == "wave":
        await fx_wave(pool, speed, duration, quiet=True)
    elif name == "breathe":
        await fx_breathe(pool, speed, duration, extra, quiet=True)
    elif name == "party":
        await fx_party(pool, speed, duration, quiet=True)
    elif name == "candle":
        await fx_candle(pool, speed, duration, quiet=True)
    elif name == "sunrise":
        mins = float(extra) if extra else 5
        await fx_sunrise(pool, mins, quiet=True)
    elif name == "ripple":
        await fx_ripple(pool, devices, speed, duration, extra, origin_idx, quiet=True)
    elif name == "chase":
        await fx_chase(pool, devices, speed, duration, origin_idx, quiet=True)
    elif name == "rain":
        await fx_rain(pool, devices, speed, duration, extra, quiet=True)
    elif name == "wipe":
        await fx_wipe(pool, devices, speed, duration, quiet=True)

    return {"effect": name, "duration": duration, "devices": len(pool.clients)}
