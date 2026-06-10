"""
Standard SynthDef library for supercollider-mcp.

All defs are built once at import time and re-sent to the server on each boot.

Available SynthDefs
-------------------
ambient_pad
    Warm, slow-attack pad. Good for chords and drones.
    Parameters: freq (440), amp (0.3), pan (0.0), attack (4.0), sustain (8.0),
                release (4.0), cutoff (1200), gate (1)

bass_drone
    Dark sawtooth sub-bass, heavily low-pass filtered.
    Parameters: freq (55), amp (0.5), pan (0.0), attack (2.0), sustain (8.0),
                release (2.0), cutoff (400), gate (1)

pluck_tone
    Karplus-Strong plucked string. Self-terminates after decay.
    Parameters: freq (440), amp (0.6), pan (0.0), decay (4.0)

noise_wind
    Band-pass filtered white noise. Good for wind, breath, texture.
    Parameters: amp (0.2), pan (0.0), attack (2.0), sustain (4.0),
                release (2.0), cutoff (800), rq (0.3), gate (1)
"""

from supriya import SynthDefBuilder
from supriya.ugens import (
    CombC,
    Dust,
    EnvGen,
    FreeVerb,
    HPF,
    LFNoise1,
    LFNoise2,
    LFSaw,
    LFTri,
    LPF,
    Mix,
    Out,
    Pan2,
    PinkNoise,
    PlayBuf,
    Pluck,
    RLPF,
    Ringz,
    Saw,
    SinOsc,
    WhiteNoise,
    XFade2,
)


def _adsr_env(builder, *, attack, sustain, release, gate="gate"):
    """ASR envelope with sustain plateau."""
    return EnvGen.kr(
        envelope=[0, 3, 1.0, attack, 1.0, sustain, 0.0, release],
        gate=builder[gate],
        done_action=2,
    )


# ---------------------------------------------------------------------------
# ambient_pad
# ---------------------------------------------------------------------------

with SynthDefBuilder(
    freq=440.0,
    amp=0.3,
    pan=0.0,
    attack=4.0,
    sustain=8.0,
    release=4.0,
    cutoff=1200.0,
    gate=1,
) as _b:
    env = _adsr_env(_b, attack=_b["attack"], sustain=_b["sustain"], release=_b["release"])
    # Two slightly detuned sines + a triangle for warmth
    detune = LFNoise1.kr(frequency=0.3) * 2
    sig = (
        SinOsc.ar(frequency=_b["freq"]) * 0.5
        + SinOsc.ar(frequency=_b["freq"] + detune) * 0.3
        + LFTri.ar(frequency=_b["freq"] * 0.5) * 0.2
    )
    sig = LPF.ar(source=sig, frequency=_b["cutoff"])
    sig = sig * env * _b["amp"]
    Out.ar(bus=0, source=Pan2.ar(source=sig, position=_b["pan"]))

ambient_pad = _b.build(name="ambient_pad")


# ---------------------------------------------------------------------------
# bass_drone
# ---------------------------------------------------------------------------

with SynthDefBuilder(
    freq=55.0,
    amp=0.5,
    pan=0.0,
    attack=2.0,
    sustain=8.0,
    release=2.0,
    cutoff=400.0,
    gate=1,
) as _b:
    env = _adsr_env(_b, attack=_b["attack"], sustain=_b["sustain"], release=_b["release"])
    sig = (
        Saw.ar(frequency=_b["freq"]) * 0.6
        + Saw.ar(frequency=_b["freq"] * 1.005) * 0.4
    )
    sig = LPF.ar(source=sig, frequency=_b["cutoff"])
    sig = sig * env * _b["amp"]
    Out.ar(bus=0, source=Pan2.ar(source=sig, position=_b["pan"]))

bass_drone = _b.build(name="bass_drone")


# ---------------------------------------------------------------------------
# pluck_tone
# ---------------------------------------------------------------------------

with SynthDefBuilder(
    freq=440.0,
    amp=0.6,
    pan=0.0,
    decay=4.0,
) as _b:
    # Karplus-Strong: excite with Dust, filter through Pluck
    excite = Dust.ar(density=300) * 0.5
    sig = Pluck.ar(
        source=excite,
        trigger=Dust.ar(density=0.001),  # near-zero so it doesn't re-trigger
        maximum_delay_time=0.1,
        delay_time=1.0 / _b["freq"],
        decay_time=_b["decay"],
        coefficient=0.5,
    )
    sig = sig * _b["amp"]
    Out.ar(bus=0, source=Pan2.ar(source=sig, position=_b["pan"]))

pluck_tone = _b.build(name="pluck_tone")


# ---------------------------------------------------------------------------
# noise_wind
# ---------------------------------------------------------------------------

with SynthDefBuilder(
    amp=0.2,
    pan=0.0,
    attack=2.0,
    sustain=4.0,
    release=2.0,
    cutoff=800.0,
    rq=0.3,
    gate=1,
) as _b:
    env = _adsr_env(_b, attack=_b["attack"], sustain=_b["sustain"], release=_b["release"])
    sig = WhiteNoise.ar()
    sig = RLPF.ar(source=sig, frequency=_b["cutoff"], reciprocal_of_q=_b["rq"])
    sig = sig * env * _b["amp"]
    Out.ar(bus=0, source=Pan2.ar(source=sig, position=_b["pan"]))

noise_wind = _b.build(name="noise_wind")


# ---------------------------------------------------------------------------
# choir_wash
# ---------------------------------------------------------------------------

with SynthDefBuilder(
    amp=0.15,
    pan=0.0,
    attack=3.0,
    sustain=6.0,
    release=4.0,
    rate=1.0,
    gate=1,
) as _b:
    env = _adsr_env(_b, attack=_b["attack"], sustain=_b["sustain"], release=_b["release"])
    # Formant-like choir: pink noise through several narrow resonant filters
    noise = PinkNoise.ar()
    f1 = Ringz.ar(source=noise, frequency=520.0 * _b["rate"], decay_time=0.04)
    f2 = Ringz.ar(source=noise, frequency=1190.0 * _b["rate"], decay_time=0.025)
    f3 = Ringz.ar(source=noise, frequency=2390.0 * _b["rate"], decay_time=0.012)
    f4 = Ringz.ar(source=noise, frequency=3500.0 * _b["rate"], decay_time=0.008)
    sig = (f1 * 0.5 + f2 * 0.3 + f3 * 0.15 + f4 * 0.05) * env * _b["amp"]
    wet = FreeVerb.ar(source=sig, room_size=0.9, damping=0.4, mix=0.85)
    Out.ar(bus=0, source=Pan2.ar(source=wet, position=_b["pan"]))

choir_wash = _b.build(name="choir_wash")


# ---------------------------------------------------------------------------
# sample_one_shot
# ---------------------------------------------------------------------------
# Plays buffer_id once from start, self-terminates.
# Parameters: buffer_id (0), amp (0.5), pan (0.0), rate (1.0)

with SynthDefBuilder(
    buffer_id=0,
    amp=0.5,
    pan=0.0,
    rate=1.0,
) as _b:
    sig = PlayBuf.ar(
        channel_count=2,
        buffer_id=_b["buffer_id"],
        rate=_b["rate"],
        done_action=2,
    )
    # PlayBuf outputs 2 channels; mix to mono then pan
    mono = (sig[0] + sig[1]) * 0.5
    Out.ar(bus=0, source=Pan2.ar(source=mono * _b["amp"], position=_b["pan"]))

sample_one_shot = _b.build(name="sample_one_shot")


# ---------------------------------------------------------------------------
# Registry: all defs in one place for easy iteration
# ---------------------------------------------------------------------------

ALL_SYNTHDEFS = {
    "ambient_pad": ambient_pad,
    "bass_drone": bass_drone,
    "pluck_tone": pluck_tone,
    "noise_wind": noise_wind,
    "choir_wash": choir_wash,
    "sample_one_shot": sample_one_shot,
}
