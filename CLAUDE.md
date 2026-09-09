# CLAUDE.md

Project-level guidance for AI coding agents (Claude Code, opencode, ...) working in this repository.

## What this project is

**ECG-Format-Converter** converts and pre-processes 12-lead ECG recordings between research formats.
Input: MATLAB `.mat`, WFDB `.dat`/`.hea`, CSV, ASC, DICOM waveform `.dcm`, vendor XML, HL7 aECG.
Output: the same seven formats — CSV, XML, DICOM, HL7 aECG, WFDB, MATLAB `.mat`, ASC — always 12
leads in the order `I,II,III,AVR,AVL,AVF,V1..V6`, plus a consolidated `ecg_summary.csv`
(21 metadata columns).
Optional pipeline: notch (50/60 Hz), bandpass 0.5-40 Hz, wavelet baseline removal, EMD denoise,
FFT/interpolation resampling, anonymization. Python 3.10+, MIT licensed. Extracted from the
beatsML pipeline.

## Essential commands

```bash
# setup (from repo root)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# convert everything in data/input_ecgs/ (demo files included) -> data/processed_ecgs/
python3 ecg_processor.py data/input_ecgs --format dcm --output data/processed_ecgs \
    --fs 500 --pipeline notch bandpass --resample 250

# pre-process into MATLAB .mat (mat and asc are selectable like any other format)
python3 ecg_processor.py data/input_ecgs --format mat --output out/ --fs 500 --pipeline bandpass

# desktop GUI
python ui.py

# inspect inputs without writing anything (readers only)
python3 debug_ecg.py data/input_ecgs
```

## Programmatic usage (preferred for agents)

```python
import sys; sys.path.insert(0, "<repo_root>")
from runner.execution_runner import ExecutionRunner

runner = ExecutionRunner(
    config_path="<repo_root>/config.ini",   # defaults to repo config if omitted
    path_source="/any/input/file/or/folder",
    path_sink="/any/output/folder",
)
runner.output_format = "csv"          # csv | xml | dcm | hl7 | wfdb | mat | asc
runner.pipeline = ["notch", "bandpass"]
runner.default_fs = 500               # needed for csv/asc inputs when filtering
runner.keep_signals = True            # get waveforms back in memory
records = runner.run()                # {record_id: {"signals": (12,n) float32,
                                      #  "metadata": {...}, "output_file": "..."}}
runner.stats                          # {"success": n, "skipped": n, "failed": n}
```

Any input/output folders work. Do **not** copy user data into `data/input_ecgs`;
pass `path_source`/`path_sink` instead. Attributes not set keep their `config.ini` defaults.

## Verifying results

- `ecg_summary.csv`: one row per successfully processed file; only written when at least one
  file succeeded (`runner.summary_path` is `None` otherwise).
- Output waveforms: exactly 12 channels in the fixed lead order; sample count changes only
  when `resample` is set.
- `original_leads` vs `calculated_leads` in the summary tell you which leads were measured
  and which were derived; leads in neither list are zero-filled.
- Failures are per file (log line + `failed` counter); one bad export never aborts the batch.
- There is no test suite. Validate changes by running the CLI end-to-end on the demo files in
  `data/input_ecgs/` — one per input format (xml, wfdb pair, mat, dcm, hl7, 8-lead csv,
  rate-less asc) — and checking the invariants above. Every writer is directly selectable, so
  cover them all: `--format csv|xml|dcm|hl7|mat|asc` plus `--format wfdb`, passing `--fs 500`
  throughout so the rate-less csv/asc inputs stay in play (wfdb skips them otherwise). Read the
  outputs back: `pydicom.dcmread` for `.dcm`, `wfdb.rdrecord` for the `.dat`/`.hea` pair,
  `scipy.io.loadmat(f)["val"]` for `.mat` (12 x n_samples), `pandas.read_csv` for `.asc`
  (n_samples x 12 under the lead-name header). `1042_demo_asc_ecg.asc` is the only demo
  `--metadata` applies to; the repo ships no demo metadata CSV, write one with ID/sex/age
  columns and 1042 as an ID.

## Behavior and gotchas

- The XML->DICOM fast path (`xml_to_dicom.py`) only fires for plain conversions: any pipeline
  step or resample target routes through the normal read -> process -> write path. Keep it
  that way; the fast path copies raw vendor samples straight through. It derives no leads, so
  it reports the leads the file carried in `original_leads` and leaves `calculated_leads`
  empty; leads the file lacked are zero-filled and listed in `missing_leads`.
- CSV/ASC readers return `fs=None`. Filtering, resampling, and WFDB output then require
  `default_fs` / `--fs`; files are skipped for processing (with a log line) if it is missing.
  When a rate *is* supplied, `convert.py` writes it back into `meta["fs"]`, so it reaches
  every writer and the summary's `sampling_freq` column instead of the writers' silent
  500 Hz fallback. Without `--fs` that column stays blank, as before.
- `output_format = "wfdb"` writes a record pair, so it breaks the one-file-per-input rule:
  `wfdb.wrsamp` emits `<stem>.dat` + `<stem>.hea` and `convert.py` uses the `.hea` as the
  record's stand-in path for the override probe, the summary `filename`, and the returned
  `output`. The writer hands `wrsamp` an explicit `fmt`/`adc_gain`/`baseline`: left to derive
  them it calls `est_res()`, which raises `min() arg is an empty sequence` on a constant
  channel — and zero-filled missing leads are exactly that. Record names are reduced to
  `[A-Za-z0-9_-]`; `wrsamp` rejects `.` outright and writes headers `rdrecord` cannot parse
  back for names with spaces or punctuation.
- `mat` and `asc` are plain one-file-per-input writers, but they carry the least metadata.
  `write_mat` stores `val` (12 x n_samples) plus `fs`, `study_id`, `age`, `sex`, `pon`, `poff`,
  `pdur` only where the source had them — `None` values are dropped rather than written empty,
  so the key set differs per record. `write_asc` is the CSV writer under a different extension:
  a lead-name header row and n_samples rows x 12 columns, nothing else. What the XML, DICOM and
  HL7 writers carry beyond that (names, birth date, weight, height, exam date) survives a
  conversion to mat/asc only in `ecg_summary.csv`.
- Amplitude heuristic in the DICOM and HL7 writers: `max(|signal|) < 30` means millivolts and
  is multiplied by 1000; storage is int16 microvolts. Do not "fix" this without checking both
  writers and the DICOM reader together.
- `override=True` (default) re-processes existing outputs; `--no-override` restores the old
  skip-if-exists behavior. The fast path honors the same flag.
- `anonymize` wipes patient ID, first/last name, and birth date in outputs and summary. Sex,
  age, weight, exam date, and the file name remain.
- EMD denoising classifies every IMF by mean frequency: above `noise_cut` (20 Hz) it is
  interval-thresholded at `threshold * MAD-sigma * sqrt(2 ln N)` with the threshold relaxed
  across detected QRS windows, below `baseline_cut` (0.5 Hz) it is dropped together with the
  trend, and the rest passes through. Do not "simplify" it back to dropping IMF 1: that shaves
  the QRS flanks and leaves the baseline wander in. The defaults in `[emd]` were picked by
  measuring SNR, R amplitude, ST level and QRS shape against synthetic ground truth — change
  them only with the same kind of evidence. `emd_denoise` needs `fs`; without it only IMF 1 is
  thresholded and the baseline is left alone.
- EMD removes the trend by default, so its output sits on the isoelectric line. `[emd]
  remove_baseline = False` restores the old offset-preserving behaviour.
- HL7 aECG files without a parsable TIME increment default to 500 Hz.
- `ExecutionRunner.run()` validates `output_format`, every `pipeline` step and
  `resample_method` before touching a file, and `_load_config` rejects a non-numeric
  `notch`/`default_fs`/`resample` naming the offending key. An unknown value used to fail
  silently per file (every input "skipped", run reported clean) - keep the checks up front.
- `ecgproc.stats` holds module-level counters; `ExecutionRunner.run()` resets them. If you call
  `ecgproc.convert.process_file` directly, call `stats.reset()` yourself.
- The metadata CSV mapping (`--metadata`) applies only to `.asc` inputs and joins on the
  numeric prefix of the file name against the CSV's ID column.
- pydicom is pinned `<3.0`: the writers use `save_as(..., write_like_original=False)`, which
  pydicom 3 removed.
- `writers._to_int16` rounds and clips before the int16 cast. A bare `astype` wraps (a 40 mV
  spike came back deep negative) and turns NaN into an arbitrary sample; do not drop it.
- `find_files` matches suffixes case-insensitively and returns them sorted, so a run is
  reproducible and a `.MAT`/`.XML` export is not silently invisible in a folder scan.
- GUI code paths must stay callback-based (`progress_cb`/`log_cb`/`cancel_cb`); no prints or
  tqdm inside `runner/` or `ecgproc/convert.py` except through the `log` parameter.

## Code map

```
runner/execution_runner.py   orchestrator: discover -> per-file convert -> metadata map -> summary
ecgproc/convert.py           per-file pipeline; returns {"status","signals","meta","output",...}
ecgproc/readers.py           mat / wfdb / csv / asc / dcm / xml / hl7 readers
ecgproc/writers.py           csv / xml / dcm / hl7 / mat / wfdb / asc writers
ecgproc/preprocess.py        notch, bandpass, wavelet, EMD, resample_signal
ecgproc/leads.py             Einthoven/Goldberger derivation of missing limb leads
ecgproc/cli.py               argparse + interactive prompts, delegates to ExecutionRunner
ui.py                        PySide6 app (ScanWorker, ProcessWorker, 12-lead viewer)
data/input_ecgs|processed_ecgs   default CLI folders (GUI passes its own)
skills/ecg-format-converter/ canonical agent skill; skills/sync.sh copies it to
                             .claude/skills/ (Claude Code) and .opencode/skills/ (opencode)
.github/workflows/build.yml  PyInstaller builds (Linux x64, Win x64, macOS arm64)
```

## Release flow (maintainers)

1. Merge to `main`, bump `__version__` in `ecgproc/__init__.py` (the GUI header reads it
   from there) and `version:` in `CITATION.cff` if needed.
2. Either trigger *Actions -> Build Executables* manually (artifacts only), or push a `v*` tag:
   the workflow then builds all three zips and attaches them to a GitHub release automatically.
