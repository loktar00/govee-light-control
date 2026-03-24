# govee-h6010

Local Bluetooth control for Govee H6010 LED bulbs. No cloud, no internet, no API key required.

Works over Bluetooth Low Energy (BLE) directly from your computer. Includes a CLI with JSON output for scripting, an MCP server for AI agent integration, 10 animated lighting effects, and 2D spatial mapping for positional effects.

## Features

- **Offline control** — works without internet using BLE
- **RGB color** — full 16M color support
- **Dedicated white LEDs** — warm (2700K) to cool (6500K) via hardware white LEDs, not RGB mixing
- **Brightness** — 1-100%
- **Batch control** — command all devices at once
- **10 lighting effects** — spectrum, wave, breathe, party, candle, sunrise, ripple, chase, rain, wipe
- **2D spatial mapping** — map your physical bulb layout for positional effects
- **CLI with `--json` mode** — structured output for scripts and AI agents
- **MCP server** — Model Context Protocol integration for Claude and other AI agents
- **Cross-platform** — Windows (primary), macOS, Linux

## Quick Start

```bash
# Install from source (or pip install govee-h6010 if published to PyPI)
pip install -e .

# Discover your bulbs
govee scan

# Turn them all on
govee all on

# Set warm white at full brightness
govee all brightness 100
govee all white 3000

# Set all to red
govee all color ff0000

# Run a rainbow effect for 30 seconds
govee fx spectrum --duration 30

# Turn them off
govee all off
```

## CLI Reference

### Device Control

```bash
govee on [device]                   # Turn on
govee off [device]                  # Turn off
govee brightness <1-100> [device]   # Set brightness
govee color <hex> [device]          # Set RGB color (#ff0000 or ff0000)
govee white [2700-6500] [device]    # Set white LED temperature (default 4000K)
govee temp <2700-6500> [device]     # Alias for white
govee status [device]               # Query device state
```

`[device]` can be a MAC address, name suffix (e.g. `C38B`), model, or index number. If only one device is cached, it can be omitted.

### Discovery & Listing

```bash
govee scan      # BLE scan for nearby Govee H6010 devices
govee list      # Show cached devices (no scan, instant)
```

### Batch Control

```bash
govee all on                    # Turn on all devices
govee all off                   # Turn off all devices
govee all brightness 75         # Set brightness on all
govee all color ff0000          # Set color on all
govee all white 3000            # Set white temperature on all
```

### Effects

```bash
govee fx spectrum                   # Rainbow color cycle
govee fx wave                       # Rainbow wave across devices
govee fx breathe [#hex]             # Pulse brightness (optional color)
govee fx party                      # Random colors, fast
govee fx candle                     # Warm flickering candlelight
govee fx sunrise [minutes]          # Deep red to warm white (default 5 min)
govee fx ripple [#hex]              # Flood fill radiating outward (2D-aware)
govee fx chase                      # Single lit bulb snake pattern (2D-aware)
govee fx rain [#hex]                # Color drops falling (2D required)
govee fx wipe                       # White/black fill top to bottom (2D required)
```

**Effect options:**
```bash
--speed <multiplier>    # Speed up/slow down (default 1.0)
--duration <seconds>    # Auto-stop after N seconds
--from <device>         # Origin point for ripple/chase (by name suffix or address)
```

**Examples:**
```bash
govee fx spectrum --speed 0.5 --duration 60
govee fx ripple --from C38B --duration 45
govee fx breathe ff0000 --speed 2
govee fx chase --from C38B --duration 30
```

### JSON Mode

Add `--json` to any command for structured JSON output:

```bash
govee scan --json
# {"ok": true, "devices": [...], "count": 12}

govee all on --json
# {"ok": true, "action": "on", "succeeded": 12, "failed": 0}

govee status --json
# {"ok": true, "device": "D0:C9:07:0E:C3:8B", "power": true, "brightness": 100, ...}
```

Errors also return JSON when in `--json` mode:
```bash
govee color xyz --json
# {"ok": false, "error": "Invalid hex color: xyz"}
```

## AI Agent Integration (MCP)

The package includes an MCP (Model Context Protocol) server that lets AI agents like Claude discover and control your lights.

### Installation

```bash
pip install "govee-h6010[mcp]"
```

### Running the Server

Start the MCP server (SSE transport on port 8765):

```bash
govee-mcp
```

The server runs at `http://localhost:8765` with the SSE endpoint at `/sse`.

### Claude Code Setup

```bash
claude mcp add govee-h6010 --transport sse --url http://localhost:8765/sse
```

### Claude Desktop Setup

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "govee-h6010": {
      "url": "http://localhost:8765/sse"
    }
  }
}
```

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `scan_devices` | BLE scan for nearby devices |
| `list_devices` | List cached devices (instant) |
| `list_effects` | List available effects with descriptions |
| `power_on(device)` | Turn on a light |
| `power_off(device)` | Turn off a light |
| `set_brightness(value, device)` | Set brightness 1-100 |
| `set_color(hex_color, device)` | Set RGB color |
| `set_white(kelvin, device)` | Set white LED temperature 2700-6500K |
| `all_on()` | Turn on all lights |
| `all_off()` | Turn off all lights |
| `all_color(hex_color)` | Set color on all lights |
| `all_white(kelvin)` | Set white on all lights |
| `run_effect(name, duration, speed)` | Run a lighting effect |
| `flash_device(device, seconds)` | Flash a device for identification |

## Setup Guide

### First Time Setup

1. **Install the package:**
   ```bash
   # From the project directory
   pip install -e .
   ```

2. **Scan for devices** (make sure Bluetooth is on):
   ```bash
   govee scan
   ```

3. **Test basic control:**
   ```bash
   govee all on
   govee all color ff0000
   govee all off
   ```

### Device Mapping (for positional effects)

To use spatial effects like ripple, chase, rain, and wipe, you need to map your physical bulb layout.

1. **Identify your bulbs** — each one lights up one at a time:
   ```bash
   govee identify
   ```
   Press Enter after noting each bulb's physical position.

2. **Create a grid file** that represents your layout. Use the device numbers from `identify`:
   ```
   6.7.....
   ....3.12
   .10.....
   ....9.8.
   2..4....
   ....1.11
   ........
   .....5..
   ```
   Use any non-digit character as empty space. Save this as `lights.grid` (or any filename).

3. **Apply the map:**
   ```bash
   govee map --grid lights.grid
   ```

4. **Or use linear mapping** if your bulbs are in a line:
   ```bash
   govee map 3,1,4,2,5,8,6,7,12,9,10,11
   ```

5. **View current map:**
   ```bash
   govee map
   ```

## BLE Protocol Reference

The H6010 uses Bluetooth Low Energy with a custom GATT service.

### Service & Characteristics

| UUID | Direction | Description |
|------|-----------|-------------|
| `00010203-0405-0607-0809-0a0b0c0d1910` | — | Primary service |
| `00010203-0405-0607-0809-0a0b0c0d2b11` | Write | Command characteristic |
| `00010203-0405-0607-0809-0a0b0c0d2b10` | Read/Notify | Response characteristic |

### Packet Format

All packets are exactly **20 bytes**: 19 bytes of data + 1 byte XOR checksum.

```
[cmd] [data...] [0x00 padding to 19 bytes] [XOR checksum]
```

The checksum is computed by XOR-ing all 19 preceding bytes.

### Commands

| Command | Bytes | Description |
|---------|-------|-------------|
| Power on | `33 01 01` | Turn on |
| Power off | `33 01 00` | Turn off |
| Brightness | `33 04 <1-100>` | Set brightness percentage |
| RGB color | `33 05 0D <R> <G> <B>` | Set RGB color (mode 0x0D for H6010) |
| White LED | `33 05 0D 00 00 00 <warmth> FF` | Dedicated white LEDs. warmth: 0x01 (2700K) to 0xFF (6500K) |
| Keepalive | `AA 01` | Sent every 0.5s to maintain connection |

**Important:** The H6010 uses mode byte `0x0D` (not `0x02`) for color commands. The white LED command uses a warmth byte that linearly maps from warm amber (0x01) to cool blue-white (0xFF), controlling dedicated warm and cool white LED channels.

## Troubleshooting

### No devices found during scan
- Ensure Bluetooth is enabled on your computer
- Move closer to the bulbs (BLE range is ~10m)
- On Windows, check that Bluetooth is not being blocked by another application
- The scan takes 8 seconds — be patient

### Connection drops during effects
- The tool sends keepalive packets every 0.5 seconds
- If devices drop, they will auto-reconnect
- The connection pool retries each device up to 3 times on startup
- Reducing `--speed` can help stability (fewer BLE writes per second)

### Lights don't respond to color commands
- Make sure lights are powered on first (`govee all on`)
- The H6010 uses mode byte 0x0D — other Govee models may use 0x02

### Windows-specific
- Windows BLE stack limits concurrent connections to ~5-7 devices
- The tool manages this automatically with connection pooling
- If you have issues, try closing the Govee app (it may hold BLE connections)

## License

MIT
