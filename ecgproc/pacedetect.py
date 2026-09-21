"""
Pacemaker pre-screen on raw ECG data.

Runs BEFORE any filtering/resampling (notch, bandpass 0.5-40 Hz, wavelet,
EMD all attenuate the 0.1-2 ms pacing spike) and reports a record-level flag
for ``ecg_summary.csv``. Source ``pacemaker`` metadata is never overwritten;
results use separate ``pacedetect_*`` keys.

Method: port of the Haq et al. 2021 / Dhar 2025 500 Hz approach to NumPy/SciPy
- high-pass (~120 Hz, Nyquist-aware) to suppress QRS/T,
- common-mode spike enhancement via SVD first principal component (PC1),
- adaptive peak picking on |PC1|,
- width check (30%-envelope, 2-30 ms) + per-lead raw-slope confirmation
  (Haq onset threshold ~13 uV/ms) on >=2 leads.

Record rule: ``n_confirmed >= min_spikes`` (scaled down for <4 s records).
No new dependencies (NumPy + SciPy only). Never raises for bad input shapes;
callers wrap in try/except and map failures to ``flag="unknown"`` anyway.

fs handling (per project decision): caller resolves
``fs_detect = meta["fs"] (file) or default_fs (CLI --fs / GUI / config)`` and
passes ``fs_source="file"|"fallback"``. ``fs=None`` or ``fs<min_fs`` yields
``unknown``. ``min_fs <= fs < low_conf_fs`` still runs but caps the score.
"""

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt

METHOD_NAME = "haq_dhar_port_v1"

# Columns appended to ecg_summary.csv (source ``pacemaker`` untouched).
PACE_SUMMARY_COLUMNS = [
    "pacedetect_flag",
    "pacedetect_score",
    "pacedetect_n_spikes",
    "pacedetect_fs",
    "pacedetect_fs_source",
    "pacedetect_method",
]

PACE_DEFAULTS = {
    "enabled": True,
    "on_threshold": 12.0,     # uV/ms - per-lead raw max-|slope| confirmation (Haq mean ~13)
    "abs_min_uV": 80.0,       # uV - floor for |PC1| peak height after 120 Hz HP
    "adaptive_k": 8.0,        # x MAD(|PC1|) - adaptive part of peak threshold
    "min_width_ms": 2.0,      # ms - 30%-envelope width lower bound
    "max_width_ms": 30.0,     # ms - upper bound (Haq max window ~27 ms)
    "refractory_ms": 20.0,    # ms - min peak separation (Dhar: 10 samples = 20 ms @500 Hz)
    "hp_cut": 120.0,          # Hz - high-pass cutoff (capped at 0.35*fs)
    "min_spikes": 3,          # confirmed spikes needed for flag=1 (>=10 s record)
    "min_leads": 2,           # leads that must confirm each candidate
    "min_fs": 100.0,          # Hz - below this: unknown (no spike bandwidth left)
    "low_conf_fs": 250.0,     # Hz - below this: run but cap score at 0.8
}


def merge_pace_options(options):
    """Validate and complete a pacemaker option dict. Raises ValueError."""
    opt = dict(PACE_DEFAULTS)
    if options:
        unknown = set(options) - set(PACE_DEFAULTS)
        if unknown:
            raise ValueError(f"Unknown pacemaker option(s): {', '.join(sorted(unknown))}")
        opt.update({k: v for k, v in options.items() if v is not None})
    opt["enabled"] = bool(opt["enabled"])
    for key in ("on_threshold", "abs_min_uV", "adaptive_k",
                "min_width_ms", "max_width_ms", "refractory_ms",
                "hp_cut", "min_fs", "low_conf_fs"):
        opt[key] = max(0.0, float(opt[key]))
    opt["min_spikes"] = max(1, int(opt["min_spikes"]))
    opt["min_leads"] = max(1, int(opt["min_leads"]))
    if opt["min_width_ms"] > opt["max_width_ms"]:
        raise ValueError("pacedetect min_width_ms exceeds max_width_ms.")
    return opt


def _mad(x):
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    sigma = float(mad) * 1.4826
    if not np.isfinite(sigma) or sigma <= 0:
        sigma = float(np.std(x))
    return sigma if np.isfinite(sigma) and sigma > 0 else 0.0


def _to_uv(signals):
    """Mirror the writers' amplitude heuristic: max|.|<30 means millivolts."""
    arr = np.asarray(signals, dtype=np.float64)
    peak = float(np.nanmax(np.abs(arr))) if arr.size else 0.0
    if peak > 0 and peak < 30.0:
        return arr * 1000.0, True
    return arr, False


def _finite_fill(arr):
    out = np.array(arr, dtype=np.float64, copy=True)
    for lead in out:
        bad = ~np.isfinite(lead)
        if bad.any():
            good = ~bad
            if good.sum() >= 2:
                lead[bad] = np.interp(np.flatnonzero(bad),
                                      np.flatnonzero(good), lead[good])
            elif good.any():
                lead[bad] = lead[good][0]
            else:
                lead[bad] = 0.0
    return out


def _highpass(arr, fs, hp_cut):
    n = arr.shape[1]
    if n < 8 or fs is None or fs <= 0:
        return arr
    cut = min(float(hp_cut), 0.35 * float(fs))
    if cut <= 0 or cut >= 0.5 * float(fs):
        return arr
    try:
        sos = butter(2, cut / (0.5 * float(fs)), btype="highpass", output="sos")
        pad = min(3 * (2 * sos.shape[0] + 1), n - 1)
        return sosfiltfilt(sos, arr, padlen=pad)
    except ValueError:
        return arr


def _detection_lead(hp):
    """SVD PC1 time course; falls back to max-energy lead."""
    try:
        xc = hp - np.mean(hp, axis=1, keepdims=True)
        if not np.all(np.isfinite(xc)) or np.all(xc == 0):
            return np.zeros(hp.shape[1])
        _, s, vt = np.linalg.svd(xc, full_matrices=False)
        pc1 = s[0] * vt[0]
        if not np.all(np.isfinite(pc1)):
            raise ValueError("non-finite PC1")
        return pc1
    except Exception:
        energy = np.sum(hp * hp, axis=1)
        best = int(np.argmax(energy)) if energy.size else 0
        return hp[best] if hp.shape[0] else np.zeros(hp.shape[1])


def _envelope_width(env, peak, frac=0.30):
    level = env[peak] * frac
    lo, hi = peak, peak
    while lo > 0 and env[lo] > level:
        lo -= 1
    while hi < env.size - 1 and env[hi] > level:
        hi += 1
    return lo, hi


def detect_pacemaker(signals, fs, signal_names=None, options=None, fs_source=""):
    """Screen raw signals for pacing spikes.

    Returns dict with flag (0|1|"unknown"), score, n_spikes, fs_used,
    fs_source, method. ``flag`` is record-level only (no per-beat output).
    """
    opt = merge_pace_options(options)
    out = {"flag": "unknown", "score": 0.0, "n_spikes": 0,
           "fs_used": fs if fs else "", "fs_source": fs_source,
           "method": METHOD_NAME}
    try:
        fs_f = float(fs) if fs else 0.0
    except (TypeError, ValueError):
        return out
    if not np.isfinite(fs_f) or fs_f < opt["min_fs"]:
        return out
    try:
        arr = np.asarray(signals)
        if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] < 8:
            return out
        arr = _finite_fill(arr)
        if not np.all(np.isfinite(arr)):
            return out
        uv, _ = _to_uv(arr)
        n = uv.shape[1]
        duration_s = n / fs_f
        if duration_s <= 0:
            return out

        hp = _highpass(uv, fs_f, opt["hp_cut"])
        pc1 = _detection_lead(hp)
        env = np.abs(pc1)
        if not np.all(np.isfinite(env)) or np.max(env) <= 0:
            out.update(flag=0, score=0.0)
            return out

        floor = _mad(env)
        thr = max(opt["adaptive_k"] * floor, opt["abs_min_uV"])
        refr = max(1, int(round(opt["refractory_ms"] / 1000.0 * fs_f)))
        peaks, _ = find_peaks(env, height=thr, distance=refr)
        if peaks.size == 0:
            out.update(flag=0, score=0.0)
            return out

        # Raw slopes in uV/ms for per-lead confirmation.
        dt_ms = 1000.0 / fs_f
        slopes = np.abs(np.diff(uv, axis=1)) / dt_ms
        slopes = np.hstack([slopes, slopes[:, -1:]])
        half_win = max(1, int(round(5.0 / 1000.0 * fs_f)))  # +-5 ms slope search
        min_w = max(1, int(round(opt["min_width_ms"] / 1000.0 * fs_f)))
        max_w = max(min_w, int(round(opt["max_width_ms"] / 1000.0 * fs_f)))

        confirmed = 0
        for p in peaks:
            lo, hi = _envelope_width(env, int(p))
            width = hi - lo + 1
            if width < min_w or width > max_w:
                continue
            s0 = max(0, int(p) - half_win)
            s1 = min(n, int(p) + half_win + 1)
            leads_hit = int(np.sum(np.max(slopes[:, s0:s1], axis=1) > opt["on_threshold"]))
            if leads_hit >= min(opt["min_leads"], uv.shape[0]):
                confirmed += 1

        need = opt["min_spikes"]
        if duration_s < 2.0:
            need = 1
        elif duration_s < 4.0:
            need = min(2, need)
        flag = 1 if confirmed >= need else 0
        score = min(1.0, confirmed / 5.0) if flag else 0.0
        if fs_f < opt["low_conf_fs"] and flag:
            score = min(score, 0.8)
        out.update(flag=flag, score=round(float(score), 3), n_spikes=int(confirmed))
        return out
    except Exception:
        return out
