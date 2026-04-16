# govee-h6010

Local control for Govee H6010 LED bulbs over Bluetooth. No cloud, no internet, no API key.

Includes a REST API server that keeps persistent BLE connections so commands hit all your bulbs in ~250ms instead of seconds. Control your lights from any device on your network with a simple HTTP call.

## Setup

Requires Python 3.10+ and Bluetooth enabled.

```bash
pip install -e ".[mcp]"
govee scan              # discover your bulbs (takes ~8 seconds)
```

## REST API Server (recommended)

The server maintains persistent BLE connections so every command is near-instant.

```bash
govee-server            # starts on port 8766, connects to all cached bulbs
# or: python -m govee_h6010.server
```

Then from any device on your network:

```bash
# Power
curl -X POST http://YOUR-PC:8766/api/all/on
curl -X POST http://YOUR-PC:8766/api/all/off

# Color / white / brightness
curl -X POST http://YOUR-PC:8766/api/all/color -d '{"hex":"#ff0000"}'
curl -X POST http://YOUR-PC:8766/api/all/white -d '{"kelvin":3000}'
curl -X POST http://YOUR-PC:8766/api/all/brightness -d '{"value":100}'

# Effects
curl -X POST http://YOUR-PC:8766/api/effect -d '{"name":"spectrum","duration":15}'
curl -X POST http://YOUR-PC:8766/api/effect -d '{"name":"party","duration":10,"speed":2.0}'

# Single device (by index, MAC, or name suffix)
curl -X POST http://YOUR-PC:8766/api/on -d '{"device":"1"}'
curl -X POST http://YOUR-PC:8766/api/color -d '{"device":"C38B","hex":"#00ff00"}'

# Status
curl http://YOUR-PC:8766/api/devices
curl http://YOUR-PC:8766/api/effects
```

All single-device endpoints also have `/api/all/*` batch variants. Effect options: `name`, `duration`, `speed`, `color`, `origin`.

## CLI

For quick one-off commands (each opens a fresh BLE connection, slower than the server):

```bash
govee all on                        # turn on all
govee all color ff0000              # set all red
govee all white 3000                # warm white
govee all brightness 100            # full brightness
govee all off                       # turn off all
govee on 1                          # single device by index
govee fx spectrum --duration 30     # effects: spectrum, wave, breathe, party,
                                    #   candle, sunrise, ripple, chase, rain, wipe
```

Add `--json` to any command for structured output.

## MCP Server (AI agent integration)

The MCP server is built into `govee-server` -- no separate process needed. The SSE endpoint is at `/sse` on the same port.

```bash
claude mcp add govee-h6010 --transport sse --url http://localhost:8766/sse
```

If you need to run MCP standalone (without the REST API):
```bash
govee-mcp                           # SSE-only on port 8765
# or: python -m govee_h6010.mcp_server
```

## Device Mapping (positional effects)

Some effects (ripple, chase, rain, wipe) use your physical bulb layout:

```bash
govee identify                      # flash each bulb to note positions
govee map --grid lights.grid        # apply a 2D ASCII grid file
govee map 3,1,4,2,5                 # or a linear ordering
```

## Troubleshooting

- **No devices found** -- make sure Bluetooth is on and you're within ~10m
- **Lights don't respond** -- power them on first (`govee all on`)
- **Windows connection limits** -- the BLE stack caps at ~5-7 concurrent connections; the server manages this automatically
- **govee-server / govee-mcp / govee not found** -- your Python Scripts dir isn't on PATH, use `python -m govee_h6010.server`, `python -m govee_h6010.mcp_server`, or `python -m govee_h6010.cli` instead

## License

MIT
