"""
Signal pre-processing for ECG records.

Every filter accepts a single lead (1-D) or a (n_leads, n_samples) array and
returns the same shape and dtype family, so the functions can be used both
inside :func:`preprocess_signals` and stand-alone.

The EMD denoiser is the only non-linear step; see :func:`emd_denoise` for what
it removes and what it deliberately keeps.
"""
import numpy as np
import pywt
from scipy.signal import butter, filtfilt, find_peaks, iirnotch, resample, sosfiltfilt

try:
    from PyEMD import EMD
    _PYEMD_AVAILABLE = True
except ImportError:
    _PYEMD_AVAILABLE = False


# --------------------------------------------------------------------- shapes

def _as_2d(signal):
    """Return (2-D float view, was_1d). Never copies unless a cast is needed."""
    arr = np.asarray(signal)
    if not np.issubdtype(arr.dtype, np.floating):
        arr = arr.astype(np.float64)
    if arr.ndim == 1:
        return arr[np.newaxis, :], True
    if arr.ndim != 2:
        raise ValueError(f"Expected a 1-D lead or a 2-D (n_leads, n_samples) array, got shape {arr.shape}.")
    return arr, False


def _restore(out, was_1d):
    return out[0] if was_1d else out


def _filtfilt(b, a, arr):
    """filtfilt with a padding length that also works on short records."""
    n = arr.shape[1]
    default_pad = 3 * max(len(a), len(b))
    if n <= 3:
        return arr.copy()
    return filtfilt(b, a, arr, axis=1, padlen=min(default_pad, n - 1))


# ------------------------------------------------------------ linear filters

def wavelet_baseline_removal(ecg_signal, wavelet='db4', level=8):
    """Remove baseline wander by subtracting the wavelet approximation."""
    arr, was_1d = _as_2d(ecg_signal)
    n = arr.shape[1]
    # A level deeper than the record supports makes pywt pad heavily and the
    # "baseline" stops being a baseline; clamp it to what the length allows.
    usable = pywt.dwt_max_level(n, pywt.Wavelet(wavelet).dec_len)
    level = max(1, min(level, usable)) if usable >= 1 else 0
    if level == 0:
        return _restore(arr.copy(), was_1d)

    out = np.empty_like(arr)
    for i in range(arr.shape[0]):
        coeffs = pywt.wavedec(arr[i, :], wavelet, level=level)
        baseline = pywt.upcoef('a', coeffs[0], wavelet, level=level, take=n)
        out[i, :] = arr[i, :] - baseline
    return _restore(out, was_1d)


def bandpass_filter(signal, lowcut=0.5, highcut=40.0, fs=500, order=3):
    """Zero-phase Butterworth bandpass. Falls back to a high-pass when the
    upper cut-off is at or above Nyquist (low sampling rates)."""
    arr, was_1d = _as_2d(signal)
    nyq = 0.5 * float(fs)
    if nyq <= 0:
        raise ValueError("Sampling frequency must be positive.")

    low = max(float(lowcut), 0.0) / nyq
    high = float(highcut) / nyq
    if low >= 1.0:
        raise ValueError(f"Bandpass low cut-off {lowcut} Hz is at or above Nyquist ({nyq} Hz).")

    if high >= 1.0:
        # Nothing to remove at the top end: keep the high-pass half only.
        b, a = butter(order, low, btype='highpass', analog=False) if low > 0 else (None, None)
        if b is None:
            return _restore(arr.copy(), was_1d)
    else:
        b, a = butter(order, [low, high], btype='band', analog=False)
    return _restore(_filtfilt(b, a, arr), was_1d)


def notch_filter(signal, notch_freq, fs=500, quality_factor=30):
    """Zero-phase IIR notch at the mains frequency."""
    arr, was_1d = _as_2d(signal)
    nyq = 0.5 * float(fs)
    norm_notch = float(notch_freq) / nyq
    if not 0 < norm_notch < 1:
        raise ValueError(f"Notch frequency {notch_freq} Hz is outside (0, {nyq}) Hz for fs={fs} Hz.")
    b, a = iirnotch(norm_notch, quality_factor)
    return _restore(_filtfilt(b, a, arr), was_1d)


# ------------------------------------------------------------- EMD denoising

# Defaults for emd_denoise(); every entry is overridable per call and, for the
# CLI/GUI, through the [emd] section of config.ini.
EMD_DEFAULTS = {
    "method": "emd",           # emd | eemd | ceemdan
    "remove_baseline": True,   # drop sub-baseline_cut IMFs and the trend
    "baseline_cut": 0.5,       # Hz — below this an IMF is baseline wander
    "noise_cut": 20.0,         # Hz — above this an IMF is noise-dominated
    "threshold": 1.0,          # universal-threshold scale (0 = keep noise IMFs whole)
    "thresholding": "hard",    # hard | soft interval thresholding
    "protect_qrs": True,       # relax the threshold inside every QRS complex
    "qrs_halfwidth": 0.06,     # s — protected half-window around each R peak
    "qrs_threshold_scale": 0.5,  # threshold multiplier inside that window (0 = keep everything)
    "adaptive_window": 0.5,    # s — window the noise level is tracked over (0 = one global estimate)
    "max_imf": None,           # cap on the number of IMFs (None = decompose fully)
    "trials": 30,              # ensemble size for eemd / ceemdan
    "noise_width": 0.05,       # ensemble noise amplitude, relative to signal range
    "seed": 0,                 # ensemble noise seed (reproducible runs)
}

_MAD_TO_SIGMA = 1.0 / 0.6745


def merge_emd_options(options):
    """Validate and complete an EMD option dict. Raises ValueError on bad input."""
    opt = dict(EMD_DEFAULTS)
    if options:
        unknown = set(options) - set(EMD_DEFAULTS)
        if unknown:
            raise ValueError(f"Unknown EMD option(s): {', '.join(sorted(unknown))}")
        opt.update({k: v for k, v in options.items() if v is not None})
    opt["method"] = str(opt["method"]).strip().lower()
    if opt["method"] not in ("emd", "eemd", "ceemdan"):
        raise ValueError(f"Unknown EMD method '{opt['method']}'. Choose from 'emd', 'eemd', 'ceemdan'.")
    opt["thresholding"] = str(opt["thresholding"]).strip().lower()
    if opt["thresholding"] not in ("hard", "soft"):
        raise ValueError(f"Unknown thresholding '{opt['thresholding']}'. Choose from 'hard', 'soft'.")
    opt["threshold"] = max(0.0, float(opt["threshold"]))
    opt["baseline_cut"] = max(0.0, float(opt["baseline_cut"]))
    opt["noise_cut"] = max(0.0, float(opt["noise_cut"]))
    opt["qrs_halfwidth"] = max(0.0, float(opt["qrs_halfwidth"]))
    opt["qrs_threshold_scale"] = min(1.0, max(0.0, float(opt["qrs_threshold_scale"])))
    opt["adaptive_window"] = max(0.0, float(opt["adaptive_window"]))
    opt["trials"] = max(2, int(opt["trials"]))
    return opt


def _mad_sigma(x):
    """Robust noise level: sparse large deflections (QRS) barely move it."""
    mad = np.median(np.abs(x - np.median(x)))
    sigma = float(mad) * _MAD_TO_SIGMA
    if sigma <= 0:
        sigma = float(np.std(x))
    return sigma


def _mean_frequency(x, fs):
    """Power-weighted mean frequency in Hz — the IMF's centre of gravity."""
    n = x.size
    if n < 8 or fs is None:
        return 0.0
    spectrum = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
    total = spectrum.sum()
    if not np.isfinite(total) or total <= 0:
        return 0.0
    freqs = np.fft.rfftfreq(n, d=1.0 / float(fs))
    return float((freqs * spectrum).sum() / total)


def _local_sigma(imf, window):
    """
    Noise level as a function of time. ECG interference is bursty — a motion
    artifact or a stretch of EMG raises the floor for a second and drops back —
    and one global estimate is then too high everywhere or too low where it
    matters. MAD over a sliding window tracks it; a QRS occupies too little of
    a window to move a median.
    """
    n = imf.size
    if window <= 0 or n < 2 * window:
        return _mad_sigma(imf)
    step = max(1, window // 2)
    starts = np.arange(0, n - window + 1, step)
    centres = starts + window / 2.0
    sigmas = np.array([_mad_sigma(imf[s:s + window]) for s in starts])
    return np.interp(np.arange(n), centres, sigmas)


def _interval_threshold(imf, thr, mode="hard"):
    """
    Interval thresholding (Kopsinis & McLaughlin): the IMF is cut at its zero
    crossings and each interval — one oscillation, one extremum — is kept or
    dropped as a whole. Sample-wise thresholding would slice through waveforms
    and leave steps behind; interval decisions cannot, because every boundary
    sits on a zero. ``thr`` is a scalar or a per-sample threshold.
    """
    if np.ndim(thr) == 0 and thr <= 0:
        return imf
    sign = np.signbit(imf)
    starts = np.concatenate(([0], np.flatnonzero(sign[1:] != sign[:-1]) + 1))
    peaks = np.maximum.reduceat(np.abs(imf), starts)
    if np.ndim(thr) == 0:
        thr = np.full(peaks.size, float(thr))
    else:
        counts = np.diff(np.append(starts, imf.size))
        thr = np.add.reduceat(thr, starts) / counts

    gain = np.ones(peaks.size)
    below = peaks <= thr
    gain[below] = 0.0
    if mode == "soft":
        above = ~below
        gain[above] = (peaks[above] - thr[above]) / peaks[above]
    if gain.all():
        return imf

    labels = np.zeros(imf.size, dtype=np.intp)
    labels[starts[1:]] = 1
    np.cumsum(labels, out=labels)
    return imf * gain[labels]


def _detect_r_peaks(lead, fs):
    """Compact Pan-Tompkins: 5-15 Hz band, derivative, square, integrate."""
    n = lead.size
    nyq = 0.5 * float(fs)
    if n < int(fs) or nyq <= 6:
        return np.empty(0, dtype=int)

    low, high = 5.0 / nyq, min(15.0, 0.9 * nyq) / nyq
    if not 0 < low < high < 1:
        return np.empty(0, dtype=int)
    try:
        sos = butter(2, [low, high], btype='band', output='sos')
        band = sosfiltfilt(sos, lead, padlen=min(3 * (2 * sos.shape[0] + 1), n - 1))
    except ValueError:
        return np.empty(0, dtype=int)

    energy = np.diff(band, prepend=band[0]) ** 2
    window = max(1, int(round(0.12 * fs)))
    envelope = np.convolve(energy, np.ones(window) / window, mode="same")

    height = 0.25 * np.percentile(envelope, 99)
    if not np.isfinite(height) or height <= 0:
        return np.empty(0, dtype=int)
    peaks, _ = find_peaks(envelope, height=height, distance=max(1, int(round(0.25 * fs))))
    if peaks.size == 0:
        return peaks

    # The integrator lags and smears; snap each detection onto the local
    # maximum of the band-passed lead so the protected window sits on the QRS.
    search = max(1, int(round(0.05 * fs)))
    refined = []
    magnitude = np.abs(band)
    for p in peaks:
        lo, hi = max(0, p - search), min(n, p + search + 1)
        refined.append(lo + int(np.argmax(magnitude[lo:hi])))
    return np.unique(np.asarray(refined, dtype=int))


def _qrs_weight(peaks, n, fs, halfwidth, taper=0.02):
    """Smooth 0..1 envelope that is 1 across every QRS complex."""
    weight = np.zeros(n)
    if peaks.size == 0 or halfwidth <= 0:
        return weight
    half = max(1, int(round(halfwidth * fs)))
    for p in peaks:
        weight[max(0, p - half):min(n, p + half + 1)] = 1.0
    ramp = max(1, int(round(taper * fs)))
    kernel = np.hanning(2 * ramp + 1)
    kernel /= kernel.sum()
    return np.clip(np.convolve(weight, kernel, mode="same"), 0.0, 1.0)


def _reference_lead(signals):
    """Index of the lead with the sharpest QRS relative to its own noise floor."""
    best, best_score = 0, -np.inf
    for i, lead in enumerate(signals):
        spread = _mad_sigma(lead)
        if spread <= 0:
            continue
        score = float(np.percentile(np.abs(lead - np.median(lead)), 99.5)) / spread
        if score > best_score:
            best, best_score = i, score
    return best


def _make_decomposer(opt):
    """Return f(x) -> IMFs, honouring the configured EMD variant."""
    max_imf = -1 if opt["max_imf"] in (None, 0) else int(opt["max_imf"])
    if opt["method"] == "emd":
        engine = EMD()
        return lambda x: engine.emd(x, max_imf=max_imf)

    from PyEMD import CEEMDAN, EEMD
    if opt["method"] == "eemd":
        engine = EEMD(trials=opt["trials"], noise_width=opt["noise_width"], parallel=False)
    else:
        engine = CEEMDAN(trials=opt["trials"], epsilon=opt["noise_width"], parallel=False)
    if opt["seed"] is not None and hasattr(engine, "noise_seed"):
        engine.noise_seed(int(opt["seed"]))
    return lambda x: engine(x, max_imf=max_imf)


def _denoise_lead(lead, fs, opt, decompose, qrs_weight):
    """Denoise one lead; returns (signal, indices of thresholded IMFs, n dropped)."""
    n = lead.size
    if n < 16 or np.ptp(lead) <= 0:
        return lead, [], 0

    imfs = np.atleast_2d(np.asarray(decompose(lead), dtype=np.float64))
    if imfs.size == 0 or imfs.shape[1] != n or imfs.shape[0] < 2:
        # One IMF means EMD found no oscillation to separate — nothing to do
        # that would not simply distort the lead.
        return lead, [], 0
    residue = lead - imfs.sum(axis=0)

    freqs = np.array([_mean_frequency(imf, fs) for imf in imfs])
    know_fs = fs is not None and fs > 0

    # Noise lives at the top of the IMF stack. With a known rate the cut is a
    # real frequency; without one, only IMF 1 is treated as noise.
    if opt["threshold"] > 0:
        noise_idx = set(np.flatnonzero(freqs > opt["noise_cut"]).tolist()) if know_fs else {0}
        if not noise_idx:
            noise_idx = {0}
    else:
        noise_idx = set()

    drop_baseline = bool(opt["remove_baseline"]) and know_fs
    universal = np.sqrt(2.0 * np.log(n))
    sigma_window = int(round(opt["adaptive_window"] * fs)) if know_fs else 0

    out = np.zeros(n)
    thresholded, dropped = [], 0
    for k, imf in enumerate(imfs):
        if drop_baseline and freqs[k] < opt["baseline_cut"]:
            dropped += 1
            continue
        if k in noise_idx:
            # MAD reads the noise floor of the IMF, not its peaks: a sparse
            # burst (QRS, pacing spike) leaves it low so the burst survives,
            # while a dense oscillation (mains hum, EMG) raises it until the
            # whole component is thresholded away.
            thr = opt["threshold"] * _local_sigma(imf, sigma_window) * universal
            cleaned = _interval_threshold(imf, thr, opt["thresholding"])
            if cleaned is not imf:
                thresholded.append(k + 1)
                if opt["protect_qrs"] and qrs_weight is not None:
                    # A QRS is a genuine burst of high-frequency energy, so the
                    # threshold is relaxed across it instead of applied blindly;
                    # pure noise inside the window still goes.
                    relaxed = _interval_threshold(imf, thr * opt["qrs_threshold_scale"],
                                                  opt["thresholding"])
                    cleaned = qrs_weight * relaxed + (1.0 - qrs_weight) * cleaned
            out += cleaned
        else:
            out += imf

    if drop_baseline and _mean_frequency(residue, fs) < opt["baseline_cut"]:
        dropped += 1
    else:
        out += residue
    return out, thresholded, dropped


def emd_denoise(ecg_signal, fs=None, options=None, log=None):
    """
    Denoise with Empirical Mode Decomposition.

    Rather than discarding the first IMF outright — which strips the sharp
    edges of every QRS complex along with the noise — each IMF is classified by
    its mean frequency and handled on its own terms:

    * IMFs above ``noise_cut`` Hz are interval-thresholded against a noise level
      tracked over ``adaptive_window`` seconds, so oscillations that stand above
      the local noise floor survive and the rest is zeroed;
    * across every detected QRS complex that threshold is relaxed by
      ``qrs_threshold_scale`` when ``protect_qrs`` is on, because a QRS is a
      genuine burst of high-frequency energy;
    * IMFs below ``baseline_cut`` Hz and the monotonic trend are dropped when
      ``remove_baseline`` is on, which is what removes baseline wander;
    * everything in between — P, QRS, T, ST — passes through untouched.

    ``fs`` is needed for the frequency classification and QRS detection. Without
    it only IMF 1 is thresholded and the baseline is left alone.

    Raises RuntimeError when PyEMD is missing.
    """
    if not _PYEMD_AVAILABLE:
        raise RuntimeError("PyEMD is not installed. Install it with 'pip install EMD-signal'.")

    opt = merge_emd_options(options)
    arr, was_1d = _as_2d(ecg_signal)
    n_leads, n = arr.shape
    if n == 0:
        return _restore(arr.copy(), was_1d)

    # Interpolate over gaps: a single NaN would spread across every IMF.
    work = np.array(arr, dtype=np.float64, copy=True)
    for lead in work:
        bad = ~np.isfinite(lead)
        if bad.any():
            good = ~bad
            lead[bad] = np.interp(np.flatnonzero(bad), np.flatnonzero(good), lead[good]) \
                if good.any() else 0.0

    # Beats are simultaneous across leads, so the QRS windows are detected once
    # on the cleanest lead and shared — more reliable on flat leads, and faster.
    qrs_weight, n_beats = None, 0
    if opt["protect_qrs"] and fs:
        peaks = _detect_r_peaks(work[_reference_lead(work)], fs)
        n_beats = int(peaks.size)
        qrs_weight = _qrs_weight(peaks, n, fs, opt["qrs_halfwidth"])

    decompose = _make_decomposer(opt)
    out = np.empty_like(work)
    all_thresholded, total_dropped, failures = set(), 0, []
    for i in range(n_leads):
        try:
            lead_out, thresholded, dropped = _denoise_lead(work[i], fs, opt, decompose, qrs_weight)
        except Exception as exc:                      # one bad lead must not lose the record
            failures.append(f"lead {i + 1}: {exc}")
            lead_out = work[i]
            thresholded, dropped = [], 0
        out[i] = lead_out
        all_thresholded.update(thresholded)
        total_dropped += dropped

    if log is not None:
        detail = f"IMFs {sorted(all_thresholded)} thresholded" if all_thresholded else "no noise IMF found"
        if opt["remove_baseline"]:
            detail += f", {total_dropped} baseline component(s) removed"
        if n_beats:
            detail += f", {n_beats} QRS window(s) protected"
        log(f"EMD ({opt['method']}): {detail}.")
        for failure in failures:
            log(f"EMD failed on {failure} — lead kept unfiltered.")

    if np.issubdtype(arr.dtype, np.floating) and arr.dtype != out.dtype:
        out = out.astype(arr.dtype)
    return _restore(out, was_1d)


# ----------------------------------------------------------------- resampling

def resample_signal(signal, original_fs, target_fs, method='fft'):
    """
    Resamples the signal from original_fs to target_fs using either FFT or
    interpolation methods.
    """
    arr, was_1d = _as_2d(signal)
    if not original_fs or not target_fs:
        raise ValueError("Both the original and the target sampling frequency must be known.")
    if original_fs == target_fs:
        return _restore(arr, was_1d)

    original_len = arr.shape[1]
    target_len = int(np.round(original_len * float(target_fs) / float(original_fs)))
    if target_len < 1:
        raise ValueError(f"Resampling {original_fs} Hz -> {target_fs} Hz leaves no samples.")
    if original_len == target_len:
        return _restore(arr, was_1d)

    if method.lower() == 'fft':
        return _restore(resample(arr, target_len, axis=1), was_1d)
    elif method.lower() == 'interpolation':
        # Endpoint-anchored linear interpolation: unlike ndimage.zoom this
        # lands on exactly target_len samples and does not invent edge values.
        src = np.linspace(0.0, original_len - 1, original_len)
        dst = np.linspace(0.0, original_len - 1, target_len)
        out = np.empty((arr.shape[0], target_len), dtype=arr.dtype)
        for i in range(arr.shape[0]):
            out[i] = np.interp(dst, src, arr[i])
        return _restore(out, was_1d)
    else:
        raise ValueError(f"Unknown resampling method '{method}'. Choose from 'fft' or 'interpolation'.")


# ------------------------------------------------------------------- pipeline

# The steps preprocess_signals understands, in the order they are usually run.
PIPELINE_STEPS = ("notch", "bandpass", "wavelet", "emd")


def preprocess_signals(signals, pipeline, fs, notch_freq, emd_options=None, log=None):
    """Applies a sequence of pre-processing functions to a signal."""
    if fs is None:
        raise ValueError("Sampling frequency (fs) must be known for processing.")

    for step in pipeline:
        if step == 'notch':
            signals = notch_filter(signals, fs=fs, notch_freq=notch_freq)
        elif step == 'bandpass':
            signals = bandpass_filter(signals, fs=fs)
        elif step == 'wavelet':
            signals = wavelet_baseline_removal(signals)
        elif step == 'emd':
            if not _PYEMD_AVAILABLE:
                if log is not None:
                    log("PyEMD not found. Skipping EMD step. Install it with 'pip install EMD-signal'.")
                continue
            signals = emd_denoise(signals, fs=fs, options=emd_options, log=log)
        else:
            # Silently ignoring a typo returns the signal unfiltered and still
            # reports success -- say so instead.
            raise ValueError(f"Unknown pre-processing step '{step}'. "
                             f"Choose from {', '.join(PIPELINE_STEPS)}.")
    return signals
