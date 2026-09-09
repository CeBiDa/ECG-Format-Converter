---
name: ecg-format-converter
description: Convert and pre-process 12-lead ECG recordings between research formats (MAT, WFDB, CSV, ASC, DICOM, vendor XML, HL7 aECG in; CSV, XML, DICOM, HL7, WFDB, MAT, ASC out) with optional notch/bandpass/wavelet/EMD filtering, resampling, lead derivation, anonymization, and a consolidated ecg_summary.csv. Use when the user has ECG signal files to convert, clean, resample, anonymize, or batch-summarize. Triggers on "convert ECG", "ECG to DICOM", "HL7 aECG", "WFDB", "preprocess ECG", "notch filter", "resample ECG", "anonymize ECG".
license: MIT
---

# ECG-Format-Converter skill

Drive the ECG-Format-Converter CLI/Python API to convert ECG signal files between formats and run
signal pre-processing. Works on single files or whole folders with mixed formats.

## When to use

- User supplies ECG signal files (.mat, .dat/.hea, .csv, .asc, .dcm, .xml, .hl7) and wants
  them converted, filtered, resampled, anonymized, or summarized into one metadata table.
- User wants the standard 12 leads in fixed order, with missing limb leads derived.

Do NOT use for: ECG PDFs or scans, rhythm interpretation or diagnosis, or formats outside
the list above.

## Setup

```bash
git clone https://github.com/CeBiDa/ECG-Format-Converter
cd ECG-Format-Converter
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # Python 3.10+
```

Non-technical users can instead download a prebuilt app from GitHub Releases
(Windows x64 / macOS arm64 + x64 / Linux x64), but agents should prefer the Python API.

## Recommended: Python API (works with arbitrary folders)

```python
import sys
sys.path.insert(0, "/path/to/ECG-Format-Converter")   # repo root

from runner.execution_runner import ExecutionRunner

runner = ExecutionRunner(
    config_path="/path/to/ECG-Format-Converter/config.ini",  # pin it: unset attrs come from here
    path_source="/abs/input",
    path_sink="/abs/output",
)
runner.output_format = "csv"              # csv | xml | dcm | hl7 | wfdb | mat | asc
runner.pipeline = ["notch", "bandpass"]   # optional, order matters
runner.notch = 50                         # or 60
runner.default_fs = 500                   # REQUIRED for csv/asc inputs when filtering
runner.resample = 250                     # optional target Hz
runner.anonymize = True                   # drop ID, names, birth date
runner.keep_signals = True                # WITHOUT this, run() returns {}
records = runner.run()
print(runner.stats, runner.summary_path)
```

- `records` is `{stem: {"signals": (12, n) float32, "metadata": {...}, "output_file": path}}`,
  populated only when `keep_signals = True` and capped at `max_kept_signals` (1000).
- Every attribute you do not set comes from `config.ini` at construction time — pass
  `config_path` explicitly for reproducible runs.
- `run()` **raises ValueError** if no supported file is found, or if `output_format` is empty.
  It does not return empty.

## CLI recipes

```bash
# folder of mixed formats -> DICOM, mains filter + bandpass, 250 Hz
python3 ecg_processor.py IN/ --format dcm --output OUT/ --fs 500 \
    --pipeline notch bandpass --notch 50 --resample 250

# --format takes any of the seven: csv | xml | dcm | hl7 | wfdb | mat | asc
python3 ecg_processor.py IN/ --format mat --output OUT/ --fs 500 --pipeline bandpass

# .asc batch with external demographics (summary columns only, see below)
python3 ecg_processor.py IN/ --format csv --output OUT/ --metadata meta.csv

# folder -> WFDB record pairs (.dat + .hea); --fs is required for rate-less inputs
python3 ecg_processor.py IN/ --format wfdb --output OUT/ --fs 500

# anonymize while converting
python3 ecg_processor.py IN/ --format csv --output OUT/ --anonymize
```

Always pass explicit paths and flags; never rely on interactive prompts in automation
(they only appear on a TTY). Omitting `--output` writes to `./output_ecgs` relative to the
CWD, and omitting `--format` silently falls back to `config.ini` (csv), so pass both.
Outputs are named `OUT/<stem>.<format>` (wfdb excepted, see below), so asking for the format
the inputs already carry while pointing `--output` at the input folder overwrites the sources.

## Input/output rules that break naive checks

- **Input discovery recurses; output is flat.** Subfolders are scanned, but every output is
  written as `OUT/<stem>.<ext>`. Same-stem files in different subfolders silently overwrite
  each other and each still counts as a success. Process one flat folder at a time, or
  de-duplicate stems first.
- **A WFDB input pair is one record; a WFDB output is two files** (`.dat` + `.hea`), so
  "files in == files out" is never a valid completeness check.
- **WFDB record names are sanitized** to `[A-Za-z0-9_-]` (everything else becomes `_`), so the
  output stem may differ from the input stem. The authoritative path is the `filename` column
  of `ecg_summary.csv` (or `result["output"]`), never the input name.
- **Exit code is 1 only when there were failures AND zero successes.** A batch with some
  failures still exits 0 — parse `runner.stats` or the `### Summary ###` block, not `$?`.
- **`run()` raises before processing anything** on an unknown `output_format`, pipeline step or
  `resample_method`, so a typo aborts the batch instead of quietly skipping every file.

## Verify

- `OUT/ecg_summary.csv` has one row per **successfully written output** — skipped and failed
  files produce no row. Its mere presence proves nothing: a stale summary from an earlier run
  survives a run in which everything was skipped. Use `runner.stats` / `runner.summary_path`
  (`None` = nothing succeeded this run), or write into a fresh output folder.
- Each output has exactly 12 channels in order `I,II,III,AVR,AVL,AVF,V1..V6`. Orientation
  differs: CSV/ASC are n_samples rows x 12 cols, MAT `val` is 12 x n, wfdb `p_signal` is
  (n, 12), DICOM waveform data is time-interleaved.
- Check `original_leads` vs `calculated_leads`; leads in neither are zero-filled. **Exception:**
  a plain vendor-XML -> DICOM conversion takes the `xml_to_dicom` fast path, which derives no
  leads: `original_leads` lists what the file carried, `calculated_leads` is always blank, and
  anything missing is zero-filled rather than computed. Add any `--pipeline` step or
  `--resample` (or convert to another format) to get derived limb leads.
- For DICOM outputs: `pydicom.dcmread(f).WaveformSequence[0].NumberOfWaveformChannels == 12`.
- For WFDB outputs: the pair `<stem>.dat` + `<stem>.hea` exists (not `<stem>.wfdb`), and
  `wfdb.rdrecord(os.path.splitext(f)[0]).p_signal.shape[1] == 12`.
- For MAT outputs: `scipy.io.loadmat(f)["val"].shape == (12, n)`. The other keys (`fs`,
  `study_id`, `age`, `sex`, `pon`, `poff`, `pdur`) are written only where the source carried
  them, so a missing key is normal, not a failure.
- For ASC outputs: byte-for-byte the CSV writer with a different extension — lead-name header
  row, then n_samples rows x 12 cols; `pandas.read_csv(f).shape == (n, 12)`.
- MAT and ASC store no names, birth date, weight, height or exam date. Converting to them
  drops what XML/DICOM/HL7 would have carried; that metadata survives only in
  `ecg_summary.csv`, so keep the summary alongside the outputs.
- Amplitudes: any record whose max |sample| is < 30 is assumed millivolts and multiplied by
  1000. DICOM/HL7 store int16 microvolts; WFDB sets units mV/uV with gain to match.

## Troubleshooting

- "Unknown sampling frequency" log lines: add `--fs <Hz>` (csv/asc carry no rate). **Only
  `--format wfdb` skips those files.** For every other output the file is still written and
  counted as a *success*, silently unfiltered and un-resampled, and the DICOM/HL7 writers
  stamp a fabricated 500 Hz into it. After any filtered run, cross-check the `sampling_freq`
  column of `ecg_summary.csv` — blank means no rate was known and pre-processing was skipped.
- A file failed: run `python3 debug_ecg.py <file>` to see which reader gave up and why.
- Everything skipped: outputs already exist and `--no-override` was set (or `override = False`
  in `config.ini`). These skips are silent — only the `Skipped: n` counter shows them.
- `--metadata` did nothing: it only applies to `.asc` inputs, joins on the leading digits of
  the file name, needs an ID column plus `basis_sex|sex|gender` and `basis_age|age`, and lands
  **only in `ecg_summary.csv`** — never in the converted files.
- EMD step very slow: expected; drop `emd` from the pipeline for large batches. `--emd_method
  ceemdan` denoises better still but takes 20-45x longer.
- EMD did nothing at all: PyEMD (`EMD-signal`) is probably not installed — the step is skipped
  with a log line and the run still reports success. Like every step, it is also skipped when
  the sampling rate is unknown.
- EMD output sits on the isoelectric line: it removes the baseline trend by default. Pass
  `--emd_keep_baseline` to keep the original offset.
- EMD left too much / too little noise: raise or lower `--emd_threshold` (default 1.0) or
  `--emd_noise_cut` (default 20.0). `--help` prints both from the same defaults the code uses.
