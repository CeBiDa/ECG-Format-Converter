[![GitHub ECG-Format-Converter](https://img.shields.io/badge/github-ECG--Format--Converter-blue?logo=github)](https://github.com/CeBiDa/ECG-Format-Converter) [![Python](https://img.shields.io/badge/Python-100_%25-blue?logo=python&logoColor=fff)](#) [![OpenCode](https://img.shields.io/badge/OpenCode-Skill-blue?logo=opencode&logoColor=fff)](#) [![ClaudeCode](https://img.shields.io/badge/Claude_Code-Skill-blue?logo=claudecode&logoColor=fff)](#)

# ECG-Format-Converter

This Python tool converts and pre-processes **12-lead ECG recordings** across the common research formats. It reads MATLAB `.mat`, WFDB `.dat`/`.hea`, CSV, ASC, DICOM waveform `.dcm`, XML, and HL7 aECG files, runs an optional signal-processing pipeline (notch, bandpass, wavelet baseline removal, EMD denoising, resampling), derives missing limb leads, optionally anonymizes patient data, and writes the result as CSV, XML, DICOM `.dcm`, HL7 aECG, WFDB `.dat`/`.hea`, MATLAB `.mat`, or ASC, together with one consolidated metadata table.

```
ECG files (mat / wfdb / csv / asc / dcm / xml / hl7)  ──►  filtered, resampled 12-lead output (csv / xml / dcm / hl7 / wfdb / mat / asc)  +  ecg_summary.csv (metadata)
```

---

## Table of contents

- [Downloads](#downloads)
- [Features](#features)
- [Quick start](#quick-start)
  * [Desktop app (GUI)](#desktop-app-gui)
  * [Command line](#command-line)
  * [Python API](#python-api)
  * [AI agent integration](#ai-agent-integration)
- [Output formats](#output-formats)
- [How it works](#how-it-works)
- [Technical stack](#technical-stack)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Debugging](#debugging)
- [Limitations and data-quality notes](#limitations-and-data-quality-notes)
- [License](#license)

---

## Downloads

Ready-to-use executables of the cross-platform GUI app are built by GitHub Actions and published in the [**Releases**](https://github.com/CeBiDa/ECG-Format-Converter/releases) tab:

| Platform                    | Artifact                               |
| --------------------------- | -------------------------------------- |
| Windows x86_64              | `ECG-Format-Converter-windows-x86_64.zip`     |
| macOS Apple Silicon (arm64) | `ECG-Format-Converter-macos-arm64.app.zip`    |
| Linux x86_64                | `ECG-Format-Converter-linux-x86_64.zip`       |

Download, unzip, and run. No Python installation required. Builds are produced with PyInstaller; pushing a `v*` tag builds all three and attaches them to a GitHub release automatically.

## Features

- **Seven input formats**: MATLAB `.mat` (keys `template`/`val`/`ecg`), WFDB `.dat`/`.hea` pairs, CSV, ASC, DICOM waveform `.dcm`, XML (`wavedata`/`ECG_RHYTHMS` schema with `patdata` demographics), and HL7 aECG (`AnnotatedECG`)
- **Seven output formats**: CSV, XML, DICOM `.dcm`, HL7 aECG, WFDB `.dat`/`.hea` record pairs, MATLAB `.mat`, and ASC
- **Signal-processing pipeline**: IIR notch (50 or 60 Hz), Butterworth bandpass 0.5-40 Hz, wavelet baseline removal (db4, level 8), EMD denoising (see [below](#emd-denoising)); steps run in the order given
- **Resampling** to any target rate via FFT or linear interpolation
- **Lead derivation**: missing limb leads are computed from Einthoven and Goldberger relations (I, II, III, aVR, aVL, aVF); every output carries the standard 12 leads in fixed order, underivable leads are zero-filled and reported
- **Metadata carried through**: patient ID, name, birth date, sex, age, weight, height, pacemaker flag, exam date and time, P-wave annotations, all collected in `ecg_summary.csv`
- **Anonymization** flag that drops patient ID, names, and birth date from outputs and the summary
- **Metadata mapping** for `.asc` batches: an external CSV with ID, sex, and age is joined onto the summary by the numeric filename prefix
- **Cross-platform desktop GUI** with live progress, per-file log, processed/skipped/failed counters, a 12-lead waveform viewer in the standard 6x2 clinical layout, and a sortable summary table
- **Batch behavior**: each file fails loudly on its own; one broken export never stops the run

## Quick start

### Desktop app (GUI)

1. Grab the executable for your OS from the [Releases](https://github.com/CeBiDa/ECG-Format-Converter/releases) page (or run `python ui.py`).
2. Select an **input** folder or single file. The app scans it immediately and shows a per-format count. You can also drop a file or folder anywhere on the window.
3. Select an **output folder** and the target **format** (CSV, XML, DICOM, HL7 aECG, WFDB, MATLAB, or ASC).
4. Tick the **filter pipeline** steps you want. Under **Sampling**, tick *Input sampling rate* and pick (or type) the rate to read CSV/ASC files at — the panel tells you how many of the selected files need it — and enable **resampling** if needed. **Anonymize** removes ID, name, and birth date.
5. Hit **Start processing**. Progress, per-file log lines, and counters update live; when the run ends, pick any record to inspect its 12-lead plot or open the summary table. Cancel stops after the current file and keeps everything already written.

Input and output both start empty — nothing is pre-selected. Demo recordings ship in
`data/input_ecgs/`, one per supported input format, so you can point the app there for a first run
without any data of your own:

| Demo file | Format | Rate | What it shows |
| --- | --- | --- | --- |
| `demo_rest_ecg.xml` | vendor XML | 500 Hz | 12 leads with full `patdata` demographics |
| `demo_wfdb_ecg.dat` + `.hea` | WFDB | 500 Hz | signals in mV, no demographics |
| `demo_matlab_ecg.mat` | MATLAB | 500 Hz | `val` matrix with `fs`, `study_id`, `age`, `sex` |
| `demo_dicom_ecg.dcm` | DICOM waveform | 500 Hz | int16 microvolts, patient module filled in |
| `demo_hl7_ecg.hl7` | HL7 aECG | 500 Hz | `AnnotatedECG` with one `SLIST_PQ` per lead |
| `demo_8lead_ecg.csv` | CSV | none | 8 leads — III, aVR, aVL, aVF get derived |
| `1042_demo_asc_ecg.asc` | ASC | none | 50 Hz mains hum and baseline drift to filter out |

The two rate-less files are the ones that need a fallback rate. `1042_demo_asc_ecg.asc` is the only
demo file `--metadata` applies to: the repo ships no metadata CSV, but any table with ID, sex and
age columns and `1042` as an ID joins onto it by the leading digits of the file name.

### Command line

```
git clone https://github.com/CeBiDa/ECG-Format-Converter && cd ECG-Format-Converter

# create & activate a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate            # Linux / macOS
# .venv\Scripts\activate             # Windows (cmd: .venv\Scripts\activate.bat)

pip install -r requirements.txt      # Python 3.10+

# convert + pre-process: everything in a folder to DICOM, notch + bandpass, resample to 250 Hz
python3 ecg_processor.py data/input_ecgs --format dcm --output data/processed_ecgs \
    --fs 500 --pipeline notch bandpass --notch 50 --resample 250 --resample_method fft

# convert a folder to MATLAB .mat, bandpass, with 500 Hz assumed for the rate-less files
python3 ecg_processor.py data/input_ecgs --format mat --output data/processed_ecgs \
    --fs 500 --pipeline bandpass

# anonymize while converting
python3 ecg_processor.py my_exports/ --format csv --output out/ --anonymize
```

Run `python3 ecg_processor.py` with no arguments for interactive prompts, and `--help` for the full flag list (`--metadata`, `--no-override`, `--config`). Defaults for every option live in `config.ini`.

### Python API

```python
from runner.execution_runner import ExecutionRunner

runner = ExecutionRunner(
    config_path="config.ini",             # optional, defaults to the repo config
    path_source="path/to/input_folder",   # file or folder
    path_sink="path/to/output_folder",
)
runner.output_format = "dcm"              # csv | xml | dcm | hl7 | wfdb | mat | asc
runner.pipeline = ["notch", "bandpass"]   # any of notch, bandpass, wavelet, emd
runner.emd_options["method"] = "emd"      # tuning for the emd step, see [emd] in config.ini
runner.resample = 250
runner.default_fs = 500                   # used when a file carries no rate
runner.anonymize = False
runner.keep_signals = True                # retain waveforms in the returned dict

records = runner.run()
# records: {record_id: {"signals": (12, n) float32 ndarray in the order
#                       I,II,III,aVR,aVL,aVF,V1..V6, "metadata": {...},
#                       "output_file": "<path>"}}
# runner.stats -> {"success": ..., "skipped": ..., "failed": ...}
# runner.summary_path -> path of ecg_summary.csv
```

`run()` also takes `progress_cb(done, total, filename)`, `log_cb(text)`, and `cancel_cb() -> bool` for embedding in other tools; the GUI is built on exactly this interface.

### AI agent integration

The repository ships agent-ready instructions. [`CLAUDE.md`](CLAUDE.md) provides project context and commands for coding agents, and [`skills/ecg-format-converter/SKILL.md`](skills/ecg-format-converter/SKILL.md) is a portable [agent skill](https://code.claude.com/docs/en/skills) that lets an agent operate the converter autonomously: setup, conversion of arbitrary folders, output verification, and troubleshooting.

The skill is already installed for both supported harnesses, so cloning the repo is enough — no copying required:

| Harness | Path | Notes |
| --- | --- | --- |
| Claude Code | [`.claude/skills/ecg-format-converter/`](.claude/skills/ecg-format-converter/) | Picked up automatically in this repo |
| opencode | [`.opencode/skills/ecg-format-converter/`](.opencode/skills/ecg-format-converter/) | opencode also reads `.claude/skills/` as a documented fallback |

To make the skill available in *every* project, install it into your user-level skill directories (`~/.claude/skills/` and `~/.config/opencode/skills/`):

```bash
sh skills/sync.sh --user
```

Both in-repo copies are generated from the canonical file under `skills/`. After editing it, run `sh skills/sync.sh` to propagate the change, or `sh skills/sync.sh --check` (exit code 1 on drift) to verify them in CI. opencode requires a `name:` field in the skill front matter; Claude Code does not, but accepts it, so the single file works unmodified in both.

## Output formats

For each input file the tool writes one output file with the same stem and the chosen extension — except WFDB, which writes the record pair `<stem>.dat` + `<stem>.hea`. All outputs carry 12 leads in the fixed order `I,II,III,AVR,AVL,AVF,V1,V2,V3,V4,V5,V6`; leads that were neither recorded nor derivable are zero-filled.

- **CSV**: n_samples rows x 12 columns, header = lead names, one amplitude value per cell.
- **XML**: a `<metadata>` block (patient, exam, lead provenance) followed by one `<lead name="...">` element per lead with the comma-separated waveform.
- **DICOM**: 12-lead ECG waveform object (SOP class 1.2.840.10008.5.1.4.1.1.9.1.1), 16-bit samples in microvolts, channel definitions labeled per lead, patient and study modules filled from the source metadata, P-wave annotations (`pon`/`poff`/`pdur`) when the source had them.
- **HL7 aECG**: `AnnotatedECG` document with patient demographics, a relative time sequence carrying the sampling period, and one `SLIST_PQ` sequence per lead in microvolts.
- **WFDB**: a PhysioNet record pair — `<stem>.hea` header plus `<stem>.dat` samples, format 16, one signal per lead. The header unit is `mV` or `uV` by the same amplitude heuristic the DICOM and HL7 writers use, with the ADC gain set so one unit is 1 µV. A sampling rate is mandatory (it goes into the header), so rate-less inputs are skipped unless `--fs` is given. Record names take only letters, digits, `-` and `_`; any other character in the file stem becomes `_`, and the run log says so.
- **MATLAB `.mat`**: a SciPy-written MAT file holding the 12 x n_samples matrix under the key `val`, plus whichever of `fs`, `study_id`, `age`, `sex` and the P-wave annotations `pon`/`poff`/`pdur` the source carried — keys with no value are dropped instead of written out.
- **ASC**: the CSV layout under the `.asc` extension — comma-separated, lead names in the header row, n_samples rows x 12 columns — which is what the ASC reader reads back.

CSV and ASC store waveforms only, and MAT just the handful of fields above, so the demographics that the XML, DICOM, and HL7 writers carry survive a conversion to those three formats only in the summary table.

A consolidated **`ecg_summary.csv`** is written next to the outputs, one row per processed file, with these 21 columns:

`filename`, `patient_id`, `patient_firstname`, `patient_lastname`, `patient_birthdate`, `patient_sex`, `exam_date`, `exam_time`, `patient_age`, `n_leads`, `n_samples`, `sampling_freq`, `signal_names`, `pon`, `poff`, `pdur`, `patient_weight`, `patient_height`, `pacemaker`, `original_leads`, `calculated_leads`

Fields absent from a given source stay empty. Note that name, ID, and birth date are **personal health information**; handle outputs accordingly or run with `--anonymize`.

## How it works

```
                     ┌──────────────────────────────────────────────────┐
ECG file ──► reader  │ mat | wfdb | csv | asc | dcm | xml | hl7         │
                     │ waveforms (leads x samples) + metadata dict      │
                     └───────────────────┬──────────────────────────────┘
                                         ▼
     ┌───────────────────────────────────────────────────────────────────┐
     │ 1. anonymize (optional): drop ID, names, birth date               │
     │ 2. pipeline (optional, in order): notch -> bandpass ->            │
     │    wavelet baseline removal -> EMD denoise                        │
     │ 3. resample (optional): FFT or linear interpolation to target Hz  │
     │ 4. impute NaN gaps by linear interpolation                        │
     │ 5. derive missing limb leads (Einthoven / Goldberger)             │
     │ 6. reorder to I,II,III,aVR,aVL,aVF,V1..V6; zero-fill the rest     │
     └───────────────────────────────────┬───────────────────────────────┘
                                         ▼
                     ┌──────────────────────────────────────────────────┐
                     │ writer: csv | xml | dcm | hl7 | wfdb | mat | asc │
                     │ + one summary row per file -> ecg_summary.csv    │
                     └──────────────────────────────────────────────────┘
```

Plain XML-to-DICOM conversions without pipeline or resampling take a direct fast path that copies the vendor waveform straight into the DICOM object.

### EMD denoising

Empirical Mode Decomposition splits each lead into intrinsic mode functions (IMFs), from the
fastest oscillation down to a monotonic trend. The naive recipe — throw away IMF 1 — throws
away the steep flanks of every QRS with it and still leaves the baseline wander in place.
Each component is instead classified by its mean frequency and handled on its own terms:

| Component                        | Treatment                                                          |
| -------------------------------- | ------------------------------------------------------------------ |
| Above `noise_cut` (20 Hz)        | Interval thresholding: the IMF is cut at its zero crossings and each oscillation is kept or dropped as a whole, so transients that stand above the noise floor survive and the rest is zeroed |
| Inside a QRS complex             | The threshold is relaxed by `qrs_threshold_scale` across a window around every detected R peak, because a QRS is a genuine burst of high-frequency energy |
| Below `baseline_cut` (0.5 Hz), and the trend | Dropped when `remove_baseline` is on — this is what removes baseline wander |
| Everything in between            | Passed through untouched: P, QRS, T and the ST segment             |

The threshold per component is `threshold x sigma x sqrt(2 ln N)`, where sigma is a median
absolute deviation tracked over a sliding `adaptive_window`. MAD reads the noise *floor*, not
the peaks: a sparse burst (QRS, pacing spike) barely moves it, so the burst survives, while a
dense oscillation (mains hum, EMG) raises it until the whole component is thresholded away.
Tracking it over a window rather than the whole record is what lets a one-second motion
artifact be removed without over-filtering the quiet minutes around it.

Measured against synthetic 12-lead records with known ground truth (white noise + 50 Hz mains
+ baseline wander, sharp QRS with a mid-QRS notch, pacing spikes and an elevated ST segment):

| Input SNR | Noisy | Old (drop IMF 1) | Current |
| --------- | ----- | ---------------- | ------- |
| 0 dB      | -5.2 dB | -4.3 dB        | **+6.6 dB** |
| 5 dB      | -3.9 dB | -3.6 dB        | **+10.5 dB** |
| 10 dB     | -3.4 dB | -3.3 dB        | **+13.0 dB** |

R-wave amplitude, measured against the PQ isoelectric line, stays within ~1 % throughout, and
on records that are already clean the step is close to a no-op (QRS shape correlation 0.9993,
R amplitude preserved to 99.4 %). Set `method = ceemdan` for another 0.5-3.5 dB where the
extra 20-45x run time is acceptable.

## Technical stack

| Layer                | Technology                                                          |
| -------------------- | ------------------------------------------------------------------- |
| Language             | Python 3.10+                                                        |
| Signal processing    | NumPy, SciPy (`iirnotch`, `butter`/`filtfilt`, `resample`), PyWavelets, EMD-signal |
| Format I/O           | pydicom (DICOM), wfdb (WFDB), XML, SciPy (MAT), pandas (CSV/ASC) |
| Desktop GUI          | PySide6 (Qt6), dark theme, threaded workers                         |
| Visualization        | Matplotlib (12-lead viewer, QtAgg canvas)                           |
| Progress UX          | tqdm (CLI), Qt signal bridge (GUI)                                  |
| Packaging            | PyInstaller (`.spec` included) + GitHub Actions multi-platform builds |

Dependencies are listed in [`requirements.txt`](requirements.txt).

## Configuration

`config.ini`, section `[processing]`, sets the defaults for the CLI and the GUI. Command-line flags and GUI controls override it per run.

| Setting              | Type   | Default | Description                                                        |
| -------------------- | ------ | ------- | ------------------------------------------------------------------ |
| `format`             | string | `csv`   | Output format: `csv`, `xml`, `dcm`, `hl7`, `wfdb`, `mat`, `asc`.   |
| `override`           | bool   | `True`  | Re-process files even if the output already exists.                |
| `pipeline`           | list   | empty   | Comma-separated steps: `notch`, `bandpass`, `wavelet`, `emd`.      |
| `notch`              | int    | `50`    | Notch filter frequency, 50 or 60 Hz.                               |
| `default_fs`         | int    | blank   | Fallback sampling rate for files that carry none.                  |
| `resample`           | int    | blank   | Target sampling rate; blank keeps the original.                    |
| `resample_method`    | string | `fft`   | `fft` or `interpolation`.                                          |
| `anonymize`          | bool   | `False` | Drop patient ID, names, and birth date from outputs.               |

Section `[emd]` tunes the EMD step; it is read only when `emd` is in the pipeline. Blank
entries keep the default.

| Setting               | Type   | Default | Description                                                       |
| --------------------- | ------ | ------- | ----------------------------------------------------------------- |
| `method`              | string | `emd`   | `emd`, `eemd`, or `ceemdan`. The ensemble variants mix modes less, at 20-45x the run time. |
| `remove_baseline`     | bool   | `True`  | Drop the sub-`baseline_cut` components and the trend.             |
| `baseline_cut`        | float  | `0.5`   | Hz below which a component counts as baseline wander.             |
| `noise_cut`           | float  | `20.0`  | Hz above which a component counts as noise.                       |
| `threshold`           | float  | `1.0`   | Threshold scale; higher removes more noise, `0` keeps the noise components whole. |
| `thresholding`        | string | `hard`  | `hard` keeps amplitudes; `soft` shrinks them (R waves land ~10 % low). |
| `protect_qrs`         | bool   | `True`  | Relax the threshold inside QRS complexes instead of applying it blindly. |
| `qrs_halfwidth`       | float  | `0.06`  | Seconds of protected half-window around each detected R peak.     |
| `qrs_threshold_scale` | float  | `0.5`   | Threshold multiplier inside that window; `0` leaves the QRS untouched. |
| `adaptive_window`     | float  | `0.5`   | Seconds the noise level is tracked over; `0` uses one global estimate. |
| `trials`              | int    | `30`    | Ensemble size for `eemd` / `ceemdan`.                             |

The same knobs are available per run as `--emd_method`, `--emd_threshold`, `--emd_noise_cut`,
`--emd_soft`, `--emd_keep_baseline`, and `--emd_no_qrs_protection`, and programmatically
through `runner.emd_options`.

## Project structure

```
ECG-Format-Converter/
├── config.ini                       # Run defaults
├── ecg_processor.py                 # CLI entry point
├── ui.py                            # PySide6 desktop app
├── debug_ecg.py                     # Input inspector (readers only, writes nothing)
├── requirements.txt
├── ECG-Format-Converter.spec               # PyInstaller build recipe
├── .github/workflows/build.yml      # Release binary CI (Win / macOS / Linux)
├── runner/
│   └── execution_runner.py          # Orchestrator: discover -> convert -> summarize
├── ecgproc/
│   ├── cli.py                       # Argument parsing + interactive prompts
│   ├── convert.py                   # Per-file read -> process -> write
│   ├── readers.py                   # mat / wfdb / csv / asc / dcm / xml / hl7
│   ├── writers.py                   # csv / xml / dcm / hl7 / mat / wfdb / asc
│   ├── preprocess.py                # notch, bandpass, wavelet, EMD, resampling
│   ├── leads.py                     # Einthoven / Goldberger lead derivation
│   ├── xml_to_dicom.py              # Direct vendor-XML -> DICOM fast path
│   ├── xml_utils.py                 # Vendor XML parsing helpers
│   ├── files.py, constants.py, stats.py, deps.py
├── skills/
│   ├── ecg-format-converter/SKILL.md    # Canonical agent skill
│   └── sync.sh                          # Copies it into .claude/ and .opencode/
├── .claude/skills/                      # Claude Code discovery path (generated)
├── .opencode/skills/                    # opencode discovery path (generated)
└── data/
    ├── input_ecgs/                  # <- put your ECG files here (one demo per input format)
    └── processed_ecgs/              # <- outputs land here
```

## Debugging

`python3 debug_ecg.py [path]` runs every supported file under a path through the readers and prints lead count, sampling rate, sample count, and every metadata field found, without writing anything. Use it first when a device export fails to convert; the per-file error tells you which reader gave up and why.

## Limitations and data-quality notes

- **CSV and ASC files carry no sampling rate.** Filtering, resampling, and WFDB output need one; pass `--fs` (CLI), tick *Input sampling rate* (GUI), or set `default_fs` in `config.ini`. Without it the file is still converted to CSV/XML/DICOM/HL7/MAT/ASC — only the pre-processing steps are skipped, with a log line — but WFDB output skips the file entirely, because the rate belongs in the header.
- **XML input targets two schemas**: the vendor `wavedata`/`ECG_RHYTHMS` layout (with `patdata` demographics) and HL7 aECG `AnnotatedECG`. Other XML dialects fail loudly per file. The XML the tool *writes* is its own generic schema and is meant for downstream consumers, not for feeding back in.
- **Amplitude heuristic for DICOM and HL7 output**: signals whose absolute maximum is below 30 are treated as millivolts and multiplied by 1000; everything is stored as int16 microvolts. Pre-scaled integer data far outside that range passes through unchanged.
- **Derived leads are estimates.** Limb leads computed from Einthoven and Goldberger relations are listed in `calculated_leads`; leads that could not be derived are zero-filled, so check `original_leads` before treating all 12 columns as measured.
- **Anonymize covers ID, names, and birth date.** Sex, age, weight, exam date, and the original file name remain; rename files separately if the name itself identifies the patient.
- **EMD is slow** (seconds per lead per recording, and `eemd`/`ceemdan` are 20-45x slower again). Keep it off for large batches unless you need it.
- **EMD removes the baseline trend by default**, so its output is centred on the isoelectric line. Set `remove_baseline = False` in `[emd]` (or pass `--emd_keep_baseline`) to keep the original offset.
- **HL7 files without a usable time increment default to 500 Hz.**
- Outputs can contain patient identifiers (PHI); never commit processed data from real recordings.

## License

Released under the MIT License, see [LICENSE.md](LICENSE.md).
