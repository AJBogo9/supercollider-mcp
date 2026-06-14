"""
Audio analysis for rendered audio files.

Turns a rendered file into measurable proxies for the things a human judges by
ear: loudness, dynamics, spectral balance, stereo image, and musical key. The
model can't hear the audio, so this gives it numbers to reason about instead.

Reads WAV/AIFF (including the 24-bit AIFF that sc_render writes) via libsndfile,
so no manual sample-width decoding is needed. Depends only on numpy, soundfile,
and pyloudnorm (no librosa).
"""

import numpy as np
import pyloudnorm as pyln
import soundfile as sf

# Krumhansl-Kessler key profiles (major / minor), used for key detection.
_KRUMHANSL_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_KRUMHANSL_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)
_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_BANDS = [
    ("sub_bass", 20, 80),
    ("bass", 80, 250),
    ("low_mid", 250, 800),
    ("mid", 800, 2000),
    ("high_mid", 2000, 6000),
    ("treble", 6000, 20000),
]


def _db(x: float) -> float:
    return float(20 * np.log10(max(x, 1e-12)))


def _loudness(y: np.ndarray, sr: int) -> dict:
    """Integrated LUFS plus a per-second short-term loudness curve and range."""
    meter = pyln.Meter(sr)
    try:
        integrated = float(meter.integrated_loudness(y))
    except Exception:
        integrated = float("-inf")
    if not np.isfinite(integrated):
        integrated = -70.0  # effectively silent

    # Short-term loudness per 1s window -> loudness range (p95 - p10), an
    # EBU-LRA-style measure of how much the loudness moves over the track.
    win = sr
    n = len(y)
    short_term = []
    for start in range(0, max(1, n - win + 1), win):
        block = y[start : start + win]
        if len(block) < int(0.4 * sr):
            continue
        try:
            lufs = float(meter.integrated_loudness(block))
        except Exception:
            continue
        if np.isfinite(lufs) and lufs > -70:
            short_term.append(round(lufs, 1))

    if len(short_term) >= 2:
        arr = np.array(short_term)
        lra = float(np.percentile(arr, 95) - np.percentile(arr, 10))
    else:
        lra = 0.0

    return {
        "integrated_lufs": round(integrated, 1),
        "loudness_range_db": round(lra, 1),
        "short_term_lufs": short_term,
    }


def _true_peak(y: np.ndarray, sr: int) -> dict:
    """Sample peak, 4x-oversampled true peak, and clipped-sample count."""
    sample_peak = float(np.max(np.abs(y))) if y.size else 0.0
    # 4x oversample (FFT resample) to estimate inter-sample peaks.
    try:
        from scipy.signal import resample_poly

        up = resample_poly(y, 4, 1, axis=0)
        true_peak = float(np.max(np.abs(up)))
    except Exception:
        true_peak = sample_peak
    clipped = int(np.sum(np.abs(y) >= 0.999))
    return {
        "sample_peak_db": round(_db(sample_peak), 1),
        "true_peak_db": round(_db(true_peak), 1),
        "clipped_samples": clipped,
    }


def _spectrum(mid: np.ndarray, sr: int) -> dict:
    """Per-band energy (dB), spectral centroid, and mud/harshness flags."""
    mag = np.abs(np.fft.rfft(mid))
    freqs = np.fft.rfftfreq(len(mid), 1 / sr)

    def band_db(lo: float, hi: float) -> float:
        m = (freqs >= lo) & (freqs < hi)
        if not np.any(m):
            return -99.0
        return round(_db(float(np.sqrt(np.mean(mag[m] ** 2)))), 1)

    bands = {name: band_db(lo, hi) for name, lo, hi in _BANDS}

    mag_sum = float(np.sum(mag))
    centroid = float(np.sum(freqs * mag) / mag_sum) if mag_sum > 0 else 0.0

    # Mud (200-500 Hz) and harshness (2-5 kHz) relative to broadband average.
    def band_energy(lo: float, hi: float) -> float:
        m = (freqs >= lo) & (freqs < hi)
        return float(np.mean(mag[m] ** 2)) if np.any(m) else 0.0

    full = (freqs >= 20) & (freqs < 20000)
    avg = float(np.mean(mag[full] ** 2)) if np.any(full) else 1e-12
    mud_ratio = _db(np.sqrt(band_energy(200, 500))) - _db(np.sqrt(avg))
    harsh_ratio = _db(np.sqrt(band_energy(2000, 5000))) - _db(np.sqrt(avg))

    return {
        "bands_db": bands,
        "centroid_hz": round(centroid, 0),
        "mud_excess_db": round(mud_ratio, 1),
        "harsh_excess_db": round(harsh_ratio, 1),
    }


def _stereo(mid: np.ndarray, side: np.ndarray, left: np.ndarray, right: np.ndarray, sr: int) -> dict:
    """Phase correlation, overall width, and a mono-bass check."""
    if np.allclose(side, 0):
        return {"mono": True}

    # Phase correlation between L and R (-1 = out of phase, 1 = identical/mono).
    if np.std(left) > 0 and np.std(right) > 0:
        correlation = float(np.corrcoef(left, right)[0, 1])
    else:
        correlation = 1.0

    mid_rms = float(np.sqrt(np.mean(mid**2)))
    side_rms = float(np.sqrt(np.mean(side**2)))
    width = side_rms / mid_rms if mid_rms > 0 else 0.0

    # Low-band width: bass should be near-mono. Compare side/mid energy below 120 Hz.
    mid_mag = np.abs(np.fft.rfft(mid))
    side_mag = np.abs(np.fft.rfft(side))
    freqs = np.fft.rfftfreq(len(mid), 1 / sr)
    low = (freqs >= 20) & (freqs < 120)
    mid_low = float(np.sqrt(np.mean(mid_mag[low] ** 2))) if np.any(low) else 0.0
    side_low = float(np.sqrt(np.mean(side_mag[low] ** 2))) if np.any(low) else 0.0
    bass_width = side_low / mid_low if mid_low > 0 else 0.0

    return {
        "mono": False,
        "correlation": round(correlation, 2),
        "width": round(width, 2),
        "bass_width": round(bass_width, 2),
    }


def _key(mid: np.ndarray, sr: int) -> dict:
    """Krumhansl-Schmuckler key detection from a chroma vector."""
    mag = np.abs(np.fft.rfft(mid))
    freqs = np.fft.rfftfreq(len(mid), 1 / sr)

    chroma = np.zeros(12)
    band = (freqs >= 50) & (freqs < 5000)
    f = freqs[band]
    m = mag[band]
    # MIDI 69 = A4 = 440 Hz; pitch class 0 = C.
    midi = 69 + 12 * np.log2(f / 440.0)
    pc = np.round(midi).astype(int) % 12
    for i in range(12):
        chroma[i] = np.sum(m[pc == i])

    if chroma.sum() == 0:
        return {"key": "unknown", "confidence": 0.0}
    chroma = chroma / chroma.sum()

    best_corr = -2.0
    best_key = ("C", "major")
    second = -2.0
    for mode, profile in (("major", _KRUMHANSL_MAJOR), ("minor", _KRUMHANSL_MINOR)):
        for tonic in range(12):
            rotated = np.roll(profile, tonic)
            c = float(np.corrcoef(chroma, rotated)[0, 1])
            if c > best_corr:
                second = best_corr
                best_corr = c
                best_key = (_PITCH_CLASSES[tonic], mode)
            elif c > second:
                second = c

    confidence = round(max(0.0, best_corr - max(second, 0.0)), 2)
    return {
        "key": f"{best_key[0]} {best_key[1]}",
        "confidence": confidence,
    }


def analyze_audio(path: str, time_series: bool = False) -> dict:
    """Analyze a rendered audio file and return a metrics dict.

    Args:
        path:        Path to a WAV/AIFF file (24-bit AIFF from sc_render is fine).
        time_series: If True, include the per-second short-term loudness curve.
    """
    y, sr = sf.read(path, always_2d=True, dtype="float64")
    # y is (frames, channels)
    if y.shape[1] >= 2:
        left, right = y[:, 0], y[:, 1]
    else:
        left = right = y[:, 0]
    mid = (left + right) / 2.0
    side = (left - right) / 2.0

    rms_db = round(_db(float(np.sqrt(np.mean(mid**2)))), 1)

    loudness = _loudness(y if y.shape[1] >= 2 else y[:, 0], sr)
    short_term = loudness.pop("short_term_lufs")
    peak = _true_peak(y, sr)
    spectrum = _spectrum(mid, sr)
    stereo = _stereo(mid, side, left, right, sr)
    key = _key(mid, sr)

    result = {
        "path": path,
        "duration_s": round(len(mid) / sr, 1),
        "sample_rate": sr,
        "channels": int(y.shape[1]),
        "rms_db": rms_db,
        "dynamic_range_db": round(peak["sample_peak_db"] - rms_db, 1),
        **loudness,
        **peak,
        **spectrum,
        "stereo": stereo,
        **key,
    }
    if time_series:
        result["short_term_lufs"] = short_term
    return result


def _notes(r: dict) -> list[str]:
    """Human-readable interpretation flags from the metrics."""
    notes = []
    lufs = r["integrated_lufs"]
    if lufs > -9:
        notes.append(f"very loud ({lufs} LUFS); streaming platforms target ~-14 and will turn this down")
    elif lufs < -20:
        notes.append(f"quiet master ({lufs} LUFS); well below the ~-14 streaming target")
    if r["true_peak_db"] > -0.3 or r["clipped_samples"] > 0:
        notes.append(f"peaks near/over 0 dB ({r['true_peak_db']} dBTP, {r['clipped_samples']} clipped): risk of distortion")
    if r["dynamic_range_db"] < 6:
        notes.append(f"low dynamic range ({r['dynamic_range_db']} dB): may sound squashed/over-compressed")
    if r["loudness_range_db"] < 2:
        notes.append("very static loudness: little dynamic movement across the track")

    if r["mud_excess_db"] > 4:
        notes.append(f"low-mid buildup (+{r['mud_excess_db']} dB at 200-500 Hz): may sound muddy/boomy")
    if r["harsh_excess_db"] > 4:
        notes.append(f"presence buildup (+{r['harsh_excess_db']} dB at 2-5 kHz): may sound harsh")
    if r["centroid_hz"] < 800:
        notes.append(f"dark mix (centroid {r['centroid_hz']:.0f} Hz)")
    elif r["centroid_hz"] > 3500:
        notes.append(f"bright mix (centroid {r['centroid_hz']:.0f} Hz)")

    st = r["stereo"]
    if st.get("mono"):
        notes.append("mono signal: no stereo width")
    else:
        if st["correlation"] < 0:
            notes.append(f"negative phase correlation ({st['correlation']}): will partially cancel in mono")
        if st["bass_width"] > 0.4:
            notes.append(f"wide low end (bass width {st['bass_width']}): keep sub mono for club/vinyl")
        if st["width"] < 0.05:
            notes.append("nearly mono: little stereo image")

    if r["confidence"] < 0.05:
        notes.append(f"key ambiguous (detected {r['key']}, low confidence)")
    if not notes:
        notes.append("no obvious technical problems detected")
    return notes


def format_report(r: dict) -> str:
    """Full multi-line report for sc_analyze."""
    st = r["stereo"]
    lines = [
        f"=== Analysis: {r['path']} ===",
        f"Duration: {r['duration_s']}s  |  {r['sample_rate']} Hz  |  {r['channels']} ch",
        "",
        "LOUDNESS & DYNAMICS",
        f"  Integrated:    {r['integrated_lufs']} LUFS",
        f"  Loudness range:{r['loudness_range_db']} dB",
        f"  True peak:     {r['true_peak_db']} dBTP  (sample peak {r['sample_peak_db']} dB, {r['clipped_samples']} clipped)",
        f"  Dynamic range: {r['dynamic_range_db']} dB  (peak - RMS)",
        "",
        "SPECTRAL BALANCE",
        f"  Centroid: {r['centroid_hz']:.0f} Hz  (low=dark, high=bright)",
    ]
    for name, val in r["bands_db"].items():
        lines.append(f"    {name:10s} {val:6.1f} dB")
    lines.append("")
    lines.append("STEREO")
    if st.get("mono"):
        lines.append("  mono signal")
    else:
        lines.append(f"  Correlation: {st['correlation']}  |  Width: {st['width']}  |  Bass width: {st['bass_width']}")
    lines.append("")
    lines.append(f"KEY: {r['key']}  (confidence {r['confidence']})")
    lines.append("")
    lines.append("NOTES")
    for n in _notes(r):
        lines.append(f"  - {n}")
    return "\n".join(lines)


def one_line_summary(r: dict) -> str:
    """Compact one-line summary for folding into sc_render output."""
    st = r["stereo"]
    width = "mono" if st.get("mono") else f"width {st['width']}"
    return (
        f"{r['integrated_lufs']} LUFS, peak {r['true_peak_db']} dBTP, "
        f"DR {r['dynamic_range_db']} dB, centroid {r['centroid_hz']:.0f} Hz, "
        f"{width}, key {r['key']}"
    )
