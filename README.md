# supercollider-mcp

An MCP server that connects Claude Code to SuperCollider, enabling AI-driven music composition with both real-time playback and non-realtime (NRT) audio rendering at 50-150x realtime speed.

## Why SuperCollider?

[SuperCollider](https://github.com/supercollider/supercollider) is a platform for audio synthesis and algorithmic composition. Its synthesis server (`scsynth`) runs independently of any frontend and accepts OSC messages, making it straightforward to drive programmatically.

Two features make it well-suited for AI-assisted composition:

- **Real-time synthesis**: send code, hear results immediately, iterate
- **Non-realtime (NRT) rendering**: process an audio score from a file at 50-150x realtime speed -- a 5-minute piece renders in 2-4 seconds, no waiting

## Requirements

- Linux (tested on Ubuntu 24.04)
- `supercollider-server` package (`scsynth` 3.13+)
- PipeWire with JACK compatibility (`pw-jack`)
- Python 3.11+

Install SuperCollider server only (no GUI needed):
```bash
sudo apt install supercollider-server
```

Create the `pw-scsynth` wrapper (routes scsynth through PipeWire/JACK):
```bash
cat > ~/.local/bin/pw-scsynth << 'EOF'
#!/bin/bash
exec pw-jack scsynth "$@"
EOF
chmod +x ~/.local/bin/pw-scsynth
```

## Installation

```bash
git clone https://github.com/AJBogo9/supercollider-mcp
cd supercollider-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

## MCP configuration

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "supercollider": {
      "command": "/path/to/supercollider-mcp/.venv/bin/python",
      "args": ["-m", "sc_mcp.server"],
      "cwd": "/path/to/supercollider-mcp"
    }
  }
}
```

## Tools

| Tool | Description |
|---|---|
| `sc_boot` | Boot scsynth (auto-called by sc_play) |
| `sc_ping` | Check server status |
| `sc_quit` | Shut down scsynth |
| `sc_play(code)` | Run Python/supriya code on the live server |
| `sc_stop` | Stop all sounds, signal song threads to exit |
| `sc_log` | Read scsynth output and exec errors |
| `sc_render(code, duration, output_path)` | NRT render to WAV |
| `save_song` / `load_song` / `list_songs` | Song library |
| `save_pattern` / `load_pattern` / `list_patterns` | Pattern snippets |

## SynthDef library

Six built-in SynthDefs available in every `sc_play` call:

- `ambient_pad` -- warm slow-attack pad, good for chords
- `bass_drone` -- dark low-pass sawtooth sub-bass
- `pluck_tone` -- Karplus-Strong plucked string
- `noise_wind` -- band-pass filtered noise (wind, breath)
- `choir_wash` -- formant-filtered pink noise (choir approximation)
- `sample_one_shot` -- one-shot buffer player for audio files

## NRT example

```python
sc_render("""
import random
# Simple ambient chord progression
chords = [[293, 369, 440], [247, 311, 392], [261, 329, 415]]
for i, freqs in enumerate(chords):
    for freq in freqs:
        with score.at(i * 6.0):
            s = score.add_synth(ambient_pad, freq=freq, amp=0.25,
                                attack=2.0, sustain=3.0, release=2.0)
        with score.at(i * 6.0 + 6.0):
            score.free_node(s)
with score.at(duration):
    score.do_nothing()
""", duration=20.0, output_path="/tmp/ambient.wav")
```

Renders 20 seconds of audio in under 0.1 seconds.

## Real-time example

```python
sc_play("""
import threading, random, time

BEAT = 60.0 / 90.0

def bass_loop():
    while not stop.is_set():
        with server.at():
            server.add_synth(bass_drone, freq=55, amp=0.4, attack=1.0, sustain=2.0, release=1.0)
        stop.wait(4 * BEAT)

def melody_loop():
    scale = [440, 494, 554, 587, 659]
    while not stop.is_set():
        with server.at():
            server.add_synth(pluck_tone, freq=random.choice(scale), amp=0.3, decay=2.0)
        stop.wait(random.uniform(0.5, 2.0))

for fn in [bass_loop, melody_loop]:
    threading.Thread(target=fn, daemon=True).start()
""")
# Stop with: sc_stop()
```

