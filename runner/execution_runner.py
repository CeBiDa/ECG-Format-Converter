"""
ExecutionRunner — orchestrates one processing run: discover files, convert and
pre-process each one, map optional metadata, write ecg_summary.csv.

Used by ecg_processor.py (CLI) and ui.py (GUI):

    from runner.execution_runner import ExecutionRunner

    runner = ExecutionRunner(
        config_path="config.ini",            # optional, defaults to repo config
        path_source="path/to/input_folder",  # file or folder
        path_sink="path/to/output_folder",
    )
    records = runner.run()
    # records: {record_id: {"signals": (12, n) float32 ndarray in the standard
    #                       lead order I..V6, "metadata": {...},
    #                       "output_file": "<path>"}}
"""
import configparser
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ecgproc import stats
from ecgproc.constants import OUTPUT_FORMATS, SUPPORTED_EXTENSIONS
from ecgproc.convert import process_file
from ecgproc.files import find_files
from ecgproc.preprocess import EMD_DEFAULTS, PIPELINE_STEPS, merge_emd_options

# Column order of ecg_summary.csv (kept identical to the original tool)
SUMMARY_COLUMNS = [
    "filename",
    "patient_id",
    "patient_firstname",
    "patient_lastname",
    "patient_birthdate",
    "patient_sex",
    "exam_date",
    "exam_time",
    "patient_age",
    "n_leads",
    "n_samples",
    "sampling_freq",
    "signal_names",
    "pon",
    "poff",
    "pdur",
    "patient_weight",
    "patient_height",
    "pacemaker",
    "original_leads",
    "calculated_leads",
]


def app_root():
    """Repository root when running from source, bundle root when frozen."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def default_config_path():
    return app_root() / "config.ini"


def load_metadata_csv(meta_path, log=print):
    """
    Load an optional metadata CSV for .asc inputs. Needs columns for ID, sex
    and age (common header variants are accepted). Returns a normalized
    DataFrame with columns ID, basis_sex, basis_age, __ID, or None.
    """
    try:
        md = pd.read_csv(meta_path, sep=None, engine="python")
    except Exception as e:
        log(f"Failed to read/parse metadata CSV: {e}")
        return None

    def _norm(s):
        return re.sub(r'[^a-z0-9]+', '', str(s).lower())

    cols_norm = {c: _norm(c) for c in md.columns}
    first_by_norm = {}
    for orig, n in cols_norm.items():
        if n not in first_by_norm:
            first_by_norm[n] = orig

    id_col = first_by_norm.get("id")
    sex_col = first_by_norm.get("basissex") or first_by_norm.get("sex") or first_by_norm.get("gender")
    age_col = first_by_norm.get("basisage") or first_by_norm.get("age")

    if not (id_col and sex_col and age_col):
        log("Metadata CSV missing required columns (need ID, basis_sex, basis_age). "
            f"Found columns: {list(md.columns)}. Skipping metadata mapping.")
        return None

    md = md.rename(columns={id_col: "ID", sex_col: "basis_sex", age_col: "basis_age"})

    def _norm_id(x):
        try:
            return str(int(float(str(x).strip())))
        except (ValueError, TypeError):
            return None

    md["__ID"] = md["ID"].apply(_norm_id)
    md = md.dropna(subset=["__ID"])
    return md


class ExecutionRunner:
    """One configured processing run. Set attributes, then call run()."""

    def __init__(self, config_path=None, path_source=None, path_sink=None):
        root = app_root()
        self.path_source = Path(path_source) if path_source else root / "data" / "input_ecgs"
        self.path_sink = Path(path_sink) if path_sink else root / "data" / "processed_ecgs"

        # Defaults, overridable via config.ini and again via attributes
        self.output_format = "csv"          # csv | xml | dcm | hl7 | wfdb | mat | asc
        self.pipeline = []                  # subset of notch, bandpass, wavelet, emd
        self.notch = 50                     # 50 or 60 Hz
        self.default_fs = None              # fallback sampling frequency
        self.resample = None                # target frequency or None
        self.resample_method = "fft"        # fft | interpolation
        self.anonymize = False
        self.override = True                # re-process even if output exists
        self.metadata_csv = None            # optional CSV for .asc inputs
        # Tuning for the 'emd' pipeline step; see ecgproc.preprocess.emd_denoise
        self.emd_options = dict(EMD_DEFAULTS)

        # Waveform retention for viewers (GUI). Off by default to keep
        # large batch runs light on memory.
        self.keep_signals = False
        self.max_kept_signals = 1000

        # Populated by run()
        self.summary = []
        self.summary_path = None
        self.stats = {"success": 0, "skipped": 0, "failed": 0}
        self.cancelled = False

        self._load_config(Path(config_path) if config_path else default_config_path())

    # ------------------------------------------------------------------ config

    def _load_config(self, config_path):
        if not config_path or not config_path.is_file():
            return
        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        if cfg.has_section("emd"):
            self._load_emd_config(cfg["emd"])
        if not cfg.has_section("processing"):
            return
        sec = cfg["processing"]

        def _int(key, default):
            raw = (sec.get(key, "") or "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                raise ValueError(f"{config_path}: [processing] {key} must be a whole "
                                 f"number of Hz, got '{raw}'.") from None

        def _bool(key, default):
            try:
                return sec.getboolean(key, default)
            except ValueError:
                raise ValueError(f"{config_path}: [processing] {key} must be true or "
                                 f"false, got '{sec.get(key)}'.") from None

        fmt = sec.get("format", self.output_format).strip().lower()
        if fmt:
            self.output_format = fmt
        pipeline_raw = sec.get("pipeline", "")
        if pipeline_raw.strip():
            self.pipeline = [p.strip().lower() for p in pipeline_raw.split(",") if p.strip()]
        self.notch = _int("notch", self.notch)
        self.default_fs = _int("default_fs", None)
        self.resample = _int("resample", None)
        self.resample_method = sec.get("resample_method", self.resample_method).strip().lower()
        self.anonymize = _bool("anonymize", self.anonymize)
        self.override = _bool("override", self.override)

    def _load_emd_config(self, sec):
        """Read the optional [emd] section, typed after the defaults."""
        for key, default in EMD_DEFAULTS.items():
            raw = sec.get(key, "").strip()
            if not raw:
                continue
            try:
                if isinstance(default, bool):
                    value = sec.getboolean(key)
                elif isinstance(default, float):
                    value = float(raw)
                elif isinstance(default, int) or key in ("max_imf", "seed"):
                    value = int(raw)
                else:
                    value = raw.lower()
            except ValueError:
                # Fail loudly (see M1): silently keeping the default hides typos.
                raise ValueError(f"config [emd] {key} has invalid value '{raw}'.") from None
            self.emd_options[key] = value

    # --------------------------------------------------------------------- run

    def run(self, progress_cb=None, log_cb=None, cancel_cb=None):
        """
        Process everything under path_source into path_sink.

        progress_cb(done, total, filename)  called before each file
        log_cb(text)                        per-file log lines
        cancel_cb() -> bool                 checked between files
        """
        log = log_cb if log_cb is not None else print
        stats.reset()
        self.summary = []
        self.cancelled = False
        records = {}

        files = find_files(self.path_source, SUPPORTED_EXTENSIONS)
        if not files:
            raise ValueError(f"No ECG files found in {self.path_source}")

        if not self.output_format:
            raise ValueError("No output format configured (set format).")
        if self.output_format not in OUTPUT_FORMATS:
            # Caught here rather than per file: an unknown format otherwise
            # skips every single input and still reports a clean run.
            raise ValueError(f"Unknown output format '{self.output_format}'. "
                             f"Choose from {', '.join(OUTPUT_FORMATS)}.")
        unknown_steps = [step for step in self.pipeline if step not in PIPELINE_STEPS]
        if unknown_steps:
            raise ValueError(f"Unknown pipeline step(s): {', '.join(unknown_steps)}. "
                             f"Choose from {', '.join(PIPELINE_STEPS)}.")
        if self.resample_method not in ("fft", "interpolation"):
            raise ValueError(f"Unknown resampling method '{self.resample_method}'. "
                             "Choose from 'fft' or 'interpolation'.")
        if self.notch not in (50, 60):
            raise ValueError(f"Unknown notch frequency '{self.notch}'. Choose 50 or 60 Hz.")
        for key, val in (("default_fs", self.default_fs), ("resample", self.resample)):
            if val is not None and val <= 0:
                raise ValueError(f"{key} must be a positive number of Hz, got '{val}'.")

        # Report a bad [emd] value once, before any file is touched.
        if "emd" in self.pipeline:
            self.emd_options = merge_emd_options(self.emd_options)

        # Pre-detect output collisions (see C3): different inputs mapping to the
        # same output file (same stem+different ext, or WFDB sanitisation).
        try:
            from ecgproc.writers import wfdb_record_name as _wrn
        except Exception:
            _wrn = lambda s: s
        _seen_out = {}
        for _f in files:
            _stem = _f.stem
            if self.output_format == "wfdb":
                _stem = _wrn(_stem)
                _out = f"{_stem}.hea (+.dat)"
            else:
                _out = f"{_stem}.{self.output_format}"
            if _out in _seen_out and _seen_out[_out] != str(_f):
                log(f"WARNING: '{_f.name}' collides with '{_seen_out[_out]}' "
                    f"-> same output '{_out}' (second will overwrite first).")
            else:
                _seen_out[_out] = str(_f)

        md = None
        has_asc = any(f.suffix.lower() == ".asc" for f in files)
        if self.metadata_csv and has_asc:
            md = load_metadata_csv(self.metadata_csv, log=log)

        self.path_sink.mkdir(parents=True, exist_ok=True)
        total = len(files)

        for i, file in enumerate(files):
            if cancel_cb is not None and cancel_cb():
                self.cancelled = True
                log(f"Cancelled after {i} of {total} files.")
                break
            if progress_cb is not None:
                progress_cb(i, total, file.name)

            try:
                result = process_file(
                    file, self.path_sink, self.output_format, self.summary,
                    self.pipeline, self.default_fs, self.resample, self.notch,
                    self.resample_method, self.anonymize,
                    override=self.override, log=log, emd_options=self.emd_options,
                )
            except Exception as e:
                stats.count_failed += 1
                log(f"Error processing {file.name}: {e}")
                continue

            if (result and result["status"] == "success" and self.keep_signals
                    and result["signals"] is not None
                    and len(records) < self.max_kept_signals):
                # file.stem collides across input formats (same.csv + same.mat);
                # keep the first key and suffix later ones so no record is lost.
                _key = file.stem
                if _key in records:
                    _n = 2
                    while f"{_key}__{_n}" in records:
                        _n += 1
                    log(f"WARNING: duplicate record id '{_key}' from '{file.name}'; "
                        f"kept as '{_key}__{_n}'.")
                    _key = f"{_key}__{_n}"
                records[_key] = {
                    "signals": np.asarray(result["signals"], dtype=np.float32),
                    "metadata": result["meta"],
                    "output_file": result["output"],
                }

        if progress_cb is not None and not self.cancelled:
            progress_cb(total, total, "")

        self._finalize_summary(md, log=log)
        self.stats = stats.snapshot()
        return records

    # ----------------------------------------------------------------- summary

    def _finalize_summary(self, md, log=print):
        df_summary = pd.DataFrame(self.summary)

        # Map sex/age from the metadata CSV onto .asc-derived rows by the
        # numeric ID prefix of the file name (see H1: previously applied to
        # every row, including .mat/.csv, and age crashed on str dtype).
        if md is not None and not df_summary.empty:
            try:
                if "patient_age" not in df_summary.columns:
                    df_summary["patient_age"] = ""

                sex_map = dict(zip(md["__ID"], md["basis_sex"].astype(str)))
                age_map = dict(zip(md["__ID"], md["basis_age"].astype(str)))

                def _extract_id_from_filename(fn):
                    base = os.path.basename(str(fn))
                    name = os.path.splitext(base)[0]
                    m = re.match(r"(\d+)", name)
                    return m.group(1) if m else None

                id_series = df_summary["filename"].apply(_extract_id_from_filename)
                # Scope to ASC inputs only; summary rows carry _input_ext when
                # written by convert.py (dropped before CSV export).
                if "_input_ext" in df_summary.columns:
                    mask = id_series.notna() & (df_summary["_input_ext"] == ".asc")
                else:
                    mask = id_series.notna()
                mapped_sex = id_series[mask].map(sex_map)
                mapped_age = id_series[mask].map(age_map)

                ix_sex = mapped_sex.notna()
                ix_age = mapped_age.notna()
                df_summary.loc[id_series[mask].index[ix_sex], "patient_sex"] = mapped_sex[ix_sex].astype(str)
                df_summary.loc[id_series[mask].index[ix_age], "patient_age"] = mapped_age[ix_age].astype(str)
            except Exception as e:
                log(f"Metadata mapping failed: {e}")

        if not df_summary.empty:
            for c in SUMMARY_COLUMNS:
                if c not in df_summary.columns:
                    df_summary[c] = ""
            df_summary = df_summary[SUMMARY_COLUMNS]
            self.summary_path = self.path_sink / "ecg_summary.csv"
            df_summary.to_csv(self.summary_path, index=False)
        else:
            self.summary_path = None

    # --------------------------------------------------------------- bootstrap

    @classmethod
    def bootstrap(cls):
        """Run with repo defaults: data/input_ecgs -> data/processed_ecgs."""
        runner = cls()
        records = runner.run()
        print("\n### Summary ###")
        print(f"Processed successfully: {runner.stats['success']}")
        print(f"Skipped: {runner.stats['skipped']}")
        print(f"Failed to process: {runner.stats['failed']}")
        if runner.summary_path:
            print(f"Metadata: {runner.summary_path}")
        return records
