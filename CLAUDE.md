# supercollider-mcp -- Claude Code Instructions

MCP server connecting Claude to SuperCollider (scsynth) via the supriya Python library.

## Prerequisites

- `supercollider-server` package installed (provides `scsynth` 3.13+)
- `pw-scsynth` wrapper script at `~/.local/bin/pw-scsynth` (routes audio via PipeWire/JACK)
- Python 3.11+ with venv at `.venv/`

## Starting the MCP server

The server is started automatically by Claude Code via `.mcp.json` in the project.
To run manually for testing:

```bash
cd /path/to/supercollider-mcp
.venv/bin/python -m sc_mcp.server
```

scsynth is booted automatically on the first `sc_play` or `sc_boot` call.
You do not need to start scsynth separately.

## pw-scsynth wrapper

scsynth requires JACK for audio output. On Ubuntu 24.04 with PipeWire,
raw JACK fails. The `pw-jack` wrapper connects scsynth to PipeWire's JACK layer.

```bash
# ~/.local/bin/pw-scsynth
#!/bin/bash
exec pw-jack scsynth "$@"
```

If scsynth fails to boot, check that pw-scsynth is executable and on PATH.

## Tools

| Tool | Description |
|---|---|
| `sc_boot` | Boot scsynth (called automatically by sc_play) |
| `sc_ping` | Check if scsynth is running |
| `sc_quit` | Shut down scsynth |
| `sc_play(code)` | Execute Python/supriya code against the live server |
| `sc_stop` | Free all synths, signal song loops to stop |
| `sc_log` | Read scsynth output and exec error log |
| `sc_render(code, duration, output_path)` | NRT render to WAV (50-150x realtime) |
| `save_song` / `load_song` / `list_songs` | Song library |
| `save_pattern` / `load_pattern` / `list_patterns` | Pattern library |

## sc_play context

Code passed to `sc_play` runs with:
- `server` -- live Server instance
- `stop` -- threading.Event; set by sc_stop. Song loops must check `stop.is_set()`
- `load_sample(path)` -- loads an audio file into a buffer, returns buffer_id (int)
- All stdlib SynthDefs: `ambient_pad`, `bass_drone`, `pluck_tone`, `noise_wind`,
  `choir_wash`, `sample_one_shot`
- Common UGens: `SinOsc`, `Saw`, `LFTri`, `LFNoise1`, `LFNoise2`, `WhiteNoise`,
  `PinkNoise`, `Dust`, `Pluck`, `EnvGen`, `Out`, `Pan2`, `LPF`, `HPF`, `RLPF`,
  `Ringz`, `CombC`, `Mix`, `FreeVerb`
- `SynthDefBuilder`, `SynthDef`, `Score`, `Envelope`, `ugens` (full module)
- `threading`, `random`, `time`

## sc_render context

Same as sc_play but with `score` (pre-created Score), `duration` (float), no `server`.
SynthDefs from stdlib are added to the score at t=0 automatically.
Custom SynthDefs must be added inside `with score.at(0): score.add_synthdefs(my_def)`.

## SynthDef stdlib

### ambient_pad
Warm slow-attack pad. Good for chords and drones.
```python
server.add_synth(ambient_pad, freq=440, amp=0.3, pan=0.0,
                 attack=4.0, sustain=8.0, release=4.0, cutoff=1200, gate=1)
```

### bass_drone
Dark sawtooth sub-bass with heavy low-pass.
```python
server.add_synth(bass_drone, freq=55, amp=0.5, pan=0.0,
                 attack=2.0, sustain=8.0, release=2.0, cutoff=400, gate=1)
```

### pluck_tone
Karplus-Strong plucked string. Self-terminates after decay.
```python
server.add_synth(pluck_tone, freq=440, amp=0.6, pan=0.0, decay=4.0)
```

### noise_wind
Band-pass filtered white noise for wind/texture.
```python
server.add_synth(noise_wind, amp=0.2, pan=0.0,
                 attack=2.0, sustain=4.0, release=2.0, cutoff=800, rq=0.3, gate=1)
```

### choir_wash
Formant-filtered pink noise approximating a choir wash. Use `rate` to shift pitch
(rate=0.5 is half frequency, dark; rate=1.5 is brighter).
```python
server.add_synth(choir_wash, amp=0.15, pan=0.0,
                 attack=3.0, sustain=6.0, release=4.0, rate=0.45, gate=1)
```

### sample_one_shot
Plays a buffer once, self-terminates. Load the buffer first with `load_sample(path)`.
```python
buf_id = load_sample("/path/to/file.mp3")
server.add_synth(sample_one_shot, buffer_id=buf_id, amp=0.5, pan=0.0, rate=1.0)
```

### kick_drum
Classic sine-based kick: exponential pitch drop from `punch` Hz to `tone` Hz over `decay` seconds.
```python
server.add_synth(kick_drum, amp=0.8, pan=0.0, attack=0.003, decay=0.4,
                 punch=180.0, tone=50.0)
```

### hihat
High-passed white noise with a short exponential decay. Set `open_hat=1` for an open hi-hat tail.
```python
server.add_synth(hihat, amp=0.4, pan=0.0, decay=0.08, cutoff=8000.0, open_hat=0.0)
```

## NRT rendering

NRT (non-realtime) renders audio at 50-150x realtime speed. A 5-minute piece
renders in 2-4 seconds. The score-based API differs from sc_play:

```python
sc_render("""
with score.at(0):
    pad = score.add_synth(ambient_pad, freq=293, amp=0.3, attack=2.0, sustain=6.0, release=2.0)
with score.at(8.0):
    score.free_node(pad)
with score.at(duration):
    score.do_nothing()
""", duration=10.0)
```

Gotchas:
- `score.do_nothing()` (no args) marks the end of the score
- Synths that use `gate=1` (ambient_pad, bass_drone, noise_wind, choir_wash) must
  be freed explicitly with `score.free_node(synth)`
- `pluck_tone` is self-terminating, no need to free it
- Random values must be pre-generated in Python before building the score
- Sample playback in NRT requires buffer allocation at t=0 (not yet supported
  out-of-the-box; use synth-only scores for NRT)

## Song loop pattern (sc_play)

```python
import threading, random, time

BEAT = 60.0 / 120.0  # seconds per beat

def my_loop():
    while not stop.is_set():
        with server.at():
            server.add_synth(ambient_pad, freq=440, amp=0.3)
        stop.wait(4 * BEAT)

t = threading.Thread(target=my_loop, daemon=True)
t.start()
```

## Known issues / limitations

- `sample_one_shot` assumes stereo (2-channel) buffers. If your sample is mono,
  define a custom SynthDef with `channel_count=1`.
- NRT rendering does not support sample playback (no buffer loading at score time).
- scsynth must be stopped (`sc_quit`) and restarted if it crashes; the MCP server
  will auto-reboot it on the next `sc_play` call.

## Directory structure

```
sc_mcp/
  __init__.py
  server.py     -- FastMCP server, tools, exec context
  stdlib.py     -- Standard SynthDef library
```
