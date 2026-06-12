"""
SuperCollider MCP Server

Connects Claude to SuperCollider (scsynth) with:
  - Real-time playback via supriya Python API
  - NRT (non-realtime) rendering at 50x+ speed
  - Pattern/song library
  - Standard SynthDef library (ambient_pad, bass_drone, pluck_tone, noise_wind)

scsynth is launched via pw-scsynth (pw-jack scsynth wrapper) on first use.
"""

import collections
import os
import pathlib
import tempfile
import threading
import time
import traceback

import supriya
from mcp.server.fastmcp import FastMCP
from supriya import Score, Server
from supriya.scsynth import Options

from .stdlib import ALL_SYNTHDEFS

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PATTERNS_DIR = pathlib.Path(
    os.environ.get("SC_PATTERNS_DIR", pathlib.Path.home() / "Documents/personal/beats/patterns")
)
SONGS_DIR = pathlib.Path(
    os.environ.get("SC_SONGS_DIR", pathlib.Path.home() / "Documents/personal/beats/songs")
)
RENDERS_DIR = pathlib.Path(
    os.environ.get("SC_RENDERS_DIR", pathlib.Path.home() / "Documents/personal/beats/renders")
)

SC_OPTIONS = Options(
    executable="pw-scsynth",
    output_bus_channel_count=2,
    memory_size=131072,  # 128 MB, enough for complex scores with samples
    maximum_node_count=4096,
    buffer_count=2048,
)

LOG_BUFFER_SIZE = 500

# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------

mcp = FastMCP("supercollider")

_server: Server | None = None
_log_lines: collections.deque[str] = collections.deque(maxlen=LOG_BUFFER_SIZE)
# Song loops check this event; sc_stop sets it to signal all loops to exit.
_song_stop: threading.Event = threading.Event()
# Loaded sample buffers: {path_str: buffer}
_buffers: dict[str, supriya.Buffer] = {}


class _LogCapture:
    def capture(self, line: str) -> None:
        _log_lines.append(line)

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other


def _get_server() -> Server:
    """Return the running Server, booting it if necessary."""
    global _server
    if _server is not None and _server.boot_status.name == "ONLINE":
        return _server

    _log_lines.clear()
    _log_lines.append("[sc-mcp] Booting scsynth via pw-scsynth...")

    server = Server()
    server.boot(options=SC_OPTIONS)

    # Hook the log capture into the process protocol
    if hasattr(server, "process_protocol") and server.process_protocol:
        server.process_protocol.captures.add(_LogCapture())

    # Send all stdlib SynthDefs
    with server.at():
        server.add_synthdefs(*ALL_SYNTHDEFS.values())
    time.sleep(0.1)

    # Wire scsynth outputs to the PipeWire default audio sink
    _connect_pw_outputs()

    _log_lines.append("[sc-mcp] scsynth online, stdlib SynthDefs loaded.")
    _server = server
    return server


def _default_pw_sink() -> str:
    """Return the name of the current PipeWire default audio sink."""
    import json
    import subprocess
    try:
        out = subprocess.check_output(["pw-metadata"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if "default.audio.sink" in line:
                # value:'{"name":"bluez_output.xxx"}' -> extract name
                start = line.index('{"name"')
                end = line.index("'", start)
                return json.loads(line[start:end])["name"]
    except Exception:
        pass
    return ""


def _connect_pw_outputs() -> None:
    """Connect SuperCollider:out_1/out_2 to the PipeWire default audio sink."""
    import subprocess
    sink = _default_pw_sink()
    if not sink:
        _log_lines.append("[sc-mcp] WARNING: could not find default PipeWire sink, audio may be silent.")
        return
    time.sleep(0.3)  # let pw-jack register SuperCollider ports
    for sc_out, sink_in in [
        ("SuperCollider:out_1", f"{sink}:playback_FL"),
        ("SuperCollider:out_2", f"{sink}:playback_FR"),
    ]:
        result = subprocess.run(["pw-link", sc_out, sink_in], capture_output=True)
        if result.returncode == 0:
            _log_lines.append(f"[sc-mcp] linked {sc_out} -> {sink_in}")
        else:
            err = result.stderr.decode().strip()
            _log_lines.append(f"[sc-mcp] pw-link {sc_out} -> {sink_in}: {err or 'failed'}")


def _exec_context(server: Server, score: Score | None = None) -> dict:
    """Build the Python exec context available in sc_play / sc_render code."""
    import supriya
    import supriya.ugens as ugens

    ctx = {
        # Server reference
        "server": server,
        # Core supriya types
        "Score": Score,
        "SynthDef": supriya.SynthDef,
        "SynthDefBuilder": supriya.SynthDefBuilder,
        "Envelope": supriya.Envelope,
        # All UGens
        "ugens": ugens,
        # Commonly used UGens at top level for convenience
        "SinOsc": ugens.SinOsc,
        "Saw": ugens.Saw,
        "LFSaw": ugens.LFSaw,
        "LFTri": ugens.LFTri,
        "LFNoise1": ugens.LFNoise1,
        "LFNoise2": ugens.LFNoise2,
        "WhiteNoise": ugens.WhiteNoise,
        "PinkNoise": ugens.PinkNoise,
        "Dust": ugens.Dust,
        "Pluck": ugens.Pluck,
        "EnvGen": ugens.EnvGen,
        "Out": ugens.Out,
        "Pan2": ugens.Pan2,
        "LPF": ugens.LPF,
        "HPF": ugens.HPF,
        "RLPF": ugens.RLPF,
        "Ringz": ugens.Ringz,
        "CombC": ugens.CombC,
        "Mix": ugens.Mix,
        "FreeVerb": ugens.FreeVerb,
        # All stdlib SynthDefs by name
        **ALL_SYNTHDEFS,
    }

    if score is not None:
        ctx["score"] = score

    # Song loop infrastructure
    ctx["stop"] = _song_stop
    ctx["threading"] = threading
    ctx["random"] = __import__("random")
    ctx["time"] = time
    ctx["load_sample"] = _load_sample

    return ctx


def _load_sample(path: str) -> int:
    """Load an audio file into a scsynth buffer. Returns the buffer id."""
    global _buffers
    server = _get_server()
    if path in _buffers:
        try:
            return int(_buffers[path])
        except Exception:
            pass
    buf = server.add_buffer(file_path=pathlib.Path(path))
    time.sleep(0.4)  # let scsynth finish reading the file
    _buffers[path] = buf
    return int(buf)


# ---------------------------------------------------------------------------
# Tools: server lifecycle
# ---------------------------------------------------------------------------


@mcp.tool()
def sc_boot() -> str:
    """Boot scsynth. Called automatically by sc_play, but call this first to pre-warm the server."""
    _get_server()
    return "scsynth is online."


@mcp.tool()
def sc_ping() -> str:
    """Check whether scsynth is running."""
    global _server
    if _server is None:
        return "scsynth is not running. Call sc_boot or sc_play to start it."
    status = _server.boot_status.name
    return f"scsynth status: {status}"


@mcp.tool()
def sc_quit() -> str:
    """Shut down scsynth. Use sc_boot to restart."""
    global _server
    if _server is None or _server.boot_status.name != "ONLINE":
        return "scsynth is not running."
    _server.quit()
    _server = None
    return "scsynth shut down."


# ---------------------------------------------------------------------------
# Tools: real-time playback
# ---------------------------------------------------------------------------


@mcp.tool()
def sc_play(code: str) -> str:
    """Execute Python/supriya code against the live scsynth server.

    scsynth is booted automatically if not running.

    The execution context provides:
      server         -- the live Server instance
      ambient_pad    -- warm slow-attack pad SynthDef
      bass_drone     -- dark filtered sawtooth SynthDef
      pluck_tone     -- Karplus-Strong plucked string SynthDef
      noise_wind     -- band-pass filtered noise SynthDef
      SinOsc, Saw, LFTri, LFNoise1/2, WhiteNoise, PinkNoise, Dust, Pluck,
      EnvGen, Out, Pan2, LPF, HPF, RLPF, Ringz, CombC, Mix, FreeVerb,
      SynthDefBuilder, SynthDef, Score, Envelope, ugens (full ugens module)

    Example -- play a chord:
      with server.at():
          server.add_synth(ambient_pad, freq=293, amp=0.3, pan=-0.3, attack=3.0, sustain=6.0, release=3.0)
          server.add_synth(ambient_pad, freq=369, amp=0.25, pan=0.0, attack=3.0, sustain=6.0, release=3.0)
          server.add_synth(ambient_pad, freq=440, amp=0.2, pan=0.3, attack=3.0, sustain=6.0, release=3.0)

    Example -- define a new SynthDef inline and play it:
      with SynthDefBuilder(freq=440.0, amp=0.3, pan=0.0) as b:
          env = EnvGen.kr(envelope=[0,1,1.0,0.01,0.0,2.0], done_action=2)
          sig = SinOsc.ar(frequency=b['freq']) * b['amp'] * env
          Out.ar(bus=0, source=Pan2.ar(source=sig, position=b['pan']))
      my_def = b.build(name='my_sine')
      with server.at():
          server.add_synthdefs(my_def)
      import time; time.sleep(0.1)
      with server.at():
          server.add_synth(my_def, freq=440, amp=0.4)

    After calling sc_play, always call sc_log to check for errors.

    Args:
        code: Python code using the supriya API.
    """
    server = _get_server()
    ctx = _exec_context(server)
    try:
        exec(compile(code, "<sc_play>", "exec"), ctx)  # noqa: S102
        return "Code executed."
    except Exception:
        tb = traceback.format_exc()
        _log_lines.append(f"[sc-mcp exec error]\n{tb}")
        return f"Execution error:\n{tb}"


@mcp.tool()
def sc_stop() -> str:
    """Stop all sounds, free all synths, and signal any running song loops to exit."""
    global _server, _song_stop
    _song_stop.set()  # signal all threads using ctx['stop']
    _song_stop = threading.Event()  # fresh event for next song
    # Update the context reference so new sc_play calls get the fresh event
    if _server is None or _server.boot_status.name != "ONLINE":
        return "All song loops stopped (scsynth was not running)."
    with _server.at():
        _server.free_group_children(_server.default_group)
    return "All synths freed and song loops signalled to stop."


@mcp.tool()
def sc_log(lines: int = 80) -> str:
    """Read the scsynth log and any Python exec errors.

    Call this after sc_play to check for errors.

    Args:
        lines: Maximum number of lines to return (default 80).
    """
    if not _log_lines:
        return "(log is empty)"
    tail = list(_log_lines)[-lines:]
    return "\n".join(tail)


# ---------------------------------------------------------------------------
# Tools: NRT rendering
# ---------------------------------------------------------------------------


@mcp.tool()
def sc_render(
    code: str,
    duration: float,
    output_path: str = "",
) -> str:
    """Render a SuperCollider score to a WAV file at faster-than-realtime speed.

    NRT rendering is 20-100x faster than real-time: a 5-minute piece renders in seconds.
    scsynth does NOT need to be running for this -- a separate NRT process is used.

    The execution context provides the same names as sc_play, PLUS:
      score          -- a pre-created Score object; populate it in your code.
      duration       -- the total duration passed to sc_render (float, in seconds)

    The SynthDefs from the stdlib (ambient_pad, bass_drone, pluck_tone, noise_wind) are
    automatically added to the score at t=0. Custom SynthDefs must be added manually
    inside the first 'with score.at(0)' block.

    IMPORTANT rules for NRT code:
      1. Add SynthDefs and start synths inside 'with score.at(time):' blocks.
      2. Mark the end of the score: 'with score.at(duration): score.do_nothing()'
      3. Synths that use gate=1 (ambient_pad, bass_drone, noise_wind) will sustain
         until freed. Free them with:
           with score.at(release_time):
               score.free_node(synth)
      4. pluck_tone is self-terminating (done_action=2), no need to free it.
      5. Random values must be pre-generated (use Python's random module or numpy),
         not computed inside the score loop.

    Example -- a simple three-note chord held for 4 seconds:
      import random
      with score.at(0):
          pad1 = score.add_synth(ambient_pad, freq=293, amp=0.3, attack=1.0, sustain=3.0, release=1.0)
          pad2 = score.add_synth(ambient_pad, freq=369, amp=0.25, attack=1.5, sustain=3.0, release=1.0)
      with score.at(4.0):
          score.free_node(pad1)
          score.free_node(pad2)
      with score.at(duration):
          score.do_nothing()

    Example -- arpeggiated plucks:
      freqs = [293, 349, 440, 587]
      for i, f in enumerate(freqs):
          with score.at(i * 0.5):
              score.add_synth(pluck_tone, freq=f, amp=0.5, decay=2.0)
      with score.at(duration):
          score.do_nothing()

    Args:
        code:        Python score-building code. Uses 'score' variable from context.
        duration:    Total render duration in seconds.
        output_path: Output WAV file path. Defaults to ~/Documents/personal/beats/renders/{timestamp}.wav
    """
    if not output_path:
        RENDERS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(RENDERS_DIR / f"render_{int(time.time())}.wav")

    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    score = Score()

    # Load stdlib SynthDefs at t=0
    with score.at(0):
        score.add_synthdefs(*ALL_SYNTHDEFS.values())

    ctx = _exec_context(server=None, score=score)  # type: ignore[arg-type]
    ctx["duration"] = duration
    # Remove server from NRT context -- it shouldn't be used
    ctx.pop("server", None)

    try:
        exec(compile(code, "<sc_render>", "exec"), ctx)  # noqa: S102
    except Exception:
        tb = traceback.format_exc()
        return f"Score-building error:\n{tb}"

    t0 = time.time()
    try:
        # supriya.render() calls asyncio.run() internally, which fails when called
        # from inside a running event loop (the MCP server). Run it in a thread
        # instead -- threads have no running event loop, so asyncio.run() works.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                supriya.render,
                score,
                output_file_path=out,
                render_directory_path=out.parent,
                options=SC_OPTIONS,
            )
            result_path, return_code = future.result()
    except Exception:
        return f"Render error:\n{traceback.format_exc()}"

    elapsed = time.time() - t0

    if return_code != 0:
        return f"scsynth NRT exited with code {return_code} after {elapsed:.1f}s."

    size_mb = result_path.stat().st_size / 1_048_576 if result_path else 0
    realtime_ratio = duration / elapsed if elapsed > 0 else 0
    return (
        f"Rendered {duration:.1f}s of audio in {elapsed:.2f}s "
        f"({realtime_ratio:.0f}x realtime).\n"
        f"Output: {result_path} ({size_mb:.1f} MB)"
    )


# ---------------------------------------------------------------------------
# Tools: pattern library
# ---------------------------------------------------------------------------


@mcp.tool()
def save_pattern(name: str, category: str, code: str) -> str:
    """Save a reusable supriya snippet to the pattern library.

    Args:
        name:     File name without extension (e.g. "lydian_pad_d")
        category: Subfolder (e.g. "pads", "drums", "bass", "melodic")
        code:     Python supriya code to save
    """
    target_dir = PATTERNS_DIR / category
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{name}.py"
    target.write_text(code)
    return f"Saved to {target}"


@mcp.tool()
def list_patterns(category: str = "") -> str:
    """List saved patterns.

    Args:
        category: Optional subfolder to filter by. Leave empty to list all.
    """
    search_root = PATTERNS_DIR / category if category else PATTERNS_DIR
    if not search_root.exists():
        return f"No patterns found. Directory does not exist: {search_root}"
    patterns = sorted(search_root.rglob("*.py"))
    if not patterns:
        return "No patterns found."
    return "\n".join(str(p.relative_to(PATTERNS_DIR)) for p in patterns)


@mcp.tool()
def load_pattern(path: str) -> str:
    """Load a pattern from the library, returning its code.

    Args:
        path: Relative path from the patterns root (e.g. "pads/lydian_pad_d.py")
    """
    target = PATTERNS_DIR / path
    if not target.exists():
        return f"Pattern not found: {target}"
    return target.read_text()


# ---------------------------------------------------------------------------
# Tools: song library
# ---------------------------------------------------------------------------


@mcp.tool()
def save_song(name: str, code: str) -> str:
    """Save a full song with auto-incrementing version number.

    Creates songs/{name}/v{N}.py where N is one higher than the current latest version.

    Args:
        name: Song folder name in snake_case (e.g. "aurora_borealis_sc")
        code: Python supriya code to save
    """
    song_dir = SONGS_DIR / name
    song_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(song_dir.glob("v*.py"), key=lambda p: int(p.stem[1:]))
    next_v = (int(existing[-1].stem[1:]) + 1) if existing else 1
    target = song_dir / f"v{next_v}.py"
    target.write_text(code)
    return f"Saved to {target}"


@mcp.tool()
def list_songs(name: str = "") -> str:
    """List saved songs.

    Args:
        name: Optional song name to list versions of. Leave empty to list all.
    """
    search_root = SONGS_DIR / name if name else SONGS_DIR
    if not search_root.exists():
        return f"No songs found. Directory does not exist: {search_root}"
    songs = sorted(search_root.rglob("*.py"))
    if not songs:
        return "No songs found."
    return "\n".join(str(p.relative_to(SONGS_DIR)) for p in songs)


@mcp.tool()
def load_song(name: str, version: int = 0) -> str:
    """Load a song from the library.

    Args:
        name:    Song folder name (e.g. "aurora_borealis_sc")
        version: Version to load. Use 0 (default) for the latest version.
    """
    song_dir = SONGS_DIR / name
    if not song_dir.exists():
        return f"Song not found: {name}"
    if version == 0:
        versions = sorted(song_dir.glob("v*.py"), key=lambda p: int(p.stem[1:]))
        if not versions:
            return f"No versions found for song: {name}"
        target = versions[-1]
    else:
        target = song_dir / f"v{version}.py"
        if not target.exists():
            return f"Version v{version} not found for song: {name}"
    return target.read_text()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
