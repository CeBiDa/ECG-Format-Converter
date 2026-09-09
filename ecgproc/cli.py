import argparse
import sys
from pathlib import Path

from tqdm import tqdm

from ecgproc.constants import OUTPUT_FORMATS
from ecgproc.deps import check_dependencies
from ecgproc.preprocess import EMD_DEFAULTS, PIPELINE_STEPS
from runner.execution_runner import ExecutionRunner, default_config_path


def _interactive():
    return sys.stdin.isatty()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ecg_processor",
        description="Unified ECG Converter and Pre-processing Tool.",
    )
    parser.add_argument("input", nargs="?", help="Input file or folder")
    parser.add_argument("--output", help="Output folder")
    parser.add_argument("--format", help="Output format", choices=OUTPUT_FORMATS)

    # Argument to toggle anonymization
    parser.add_argument("--anonymize", action="store_true",
                        help="Anonymize patient data (removes ID, first name, last name, birthdate)")

    # Pre-processing arguments
    parser.add_argument("--pipeline", nargs='+',
                        choices=list(PIPELINE_STEPS), default=None,
                        help="Sequence of processing steps to apply.")
    parser.add_argument("--fs", type=int,
                        help="Sampling rate in Hz to assume for files that do not store one "
                             "(CSV, ASC, some MAT). Files that carry their own rate keep it. "
                             "Without this, those files are still converted but not filtered "
                             "or resampled - and are skipped entirely by --format wfdb, "
                             "which cannot write a header without a rate.")
    parser.add_argument("--resample", type=int,
                        help="Resample ECG to this target frequency in Hz.")
    parser.add_argument("--resample_method", type=str, choices=['fft', 'interpolation'],
                        default=None,
                        help="Method for resampling ('fft' or 'interpolation'). Default is 'fft'.")
    parser.add_argument("--notch", type=int, choices=[50, 60], default=None,
                        help="Frequency for the notch filter (50 or 60 Hz). Default is 50, "
                             "or whatever config.ini sets.")

    # EMD denoising options (only used when 'emd' is in the pipeline)
    emd_group = parser.add_argument_group("EMD denoising")
    emd_group.add_argument("--emd_method", choices=['emd', 'eemd', 'ceemdan'], default=None,
                           help="Decomposition used by the EMD step. 'eemd'/'ceemdan' mix in "
                                f"less, at a large cost in run time. Default is "
                                f"'{EMD_DEFAULTS['method']}'.")
    emd_group.add_argument("--emd_threshold", type=float, default=None,
                           help=f"Threshold scale for the noise IMFs; higher removes more noise. "
                                f"0 keeps them whole. Default is {EMD_DEFAULTS['threshold']}.")
    emd_group.add_argument("--emd_noise_cut", type=float, default=None,
                           help="Hz above which an IMF counts as noise. "
                                f"Default is {EMD_DEFAULTS['noise_cut']}.")
    emd_group.add_argument("--emd_soft", dest="emd_thresholding", action="store_const",
                           const="soft", default=None,
                           help="Shrink thresholded intervals instead of keeping them intact.")
    emd_group.add_argument("--emd_keep_baseline", dest="emd_remove_baseline",
                           action="store_false", default=None,
                           help="Keep the low-frequency trend (EMD removes baseline wander by default).")
    emd_group.add_argument("--emd_no_qrs_protection", dest="emd_protect_qrs",
                           action="store_false", default=None,
                           help="Threshold inside QRS complexes too (not recommended).")

    parser.add_argument("--metadata",
                        help="Metadata CSV (ID, sex, age) mapped onto .asc inputs by filename ID.")
    parser.add_argument("--no-override", dest="override", action="store_false", default=None,
                        help="Skip files whose output already exists instead of re-processing them.")
    parser.add_argument("--config", help=f"Config file (default: {default_config_path()})")
    return parser


def main(argv=None):
    check_dependencies()
    args = build_parser().parse_args(argv)

    # Interactive fallbacks, matching the original tool
    if not args.input:
        if not _interactive():
            print("Error: no input path given. Usage: ecg_processor.py <input> "
                  f"--format <{'|'.join(OUTPUT_FORMATS)}> --output <folder>")
            sys.exit(2)
        args.input = input("Enter input file or folder path: ").strip()
    if not args.format and _interactive():
        entered = input(f"Enter conversion type [{'/'.join(OUTPUT_FORMATS)}] (empty = value from config): ").strip().lower()
        if entered:
            if entered not in OUTPUT_FORMATS:
                print(f"Unknown format '{entered}'.")
                sys.exit(2)
            args.format = entered
    if not args.output and _interactive():
        args.output = input("Enter output folder: ").strip() or "output_ecgs"

    runner = ExecutionRunner(
        config_path=args.config,
        path_source=args.input,
        path_sink=args.output or "output_ecgs",
    )
    if args.format:
        runner.output_format = args.format
    if args.pipeline is not None:
        runner.pipeline = args.pipeline
    if args.fs is not None:
        runner.default_fs = args.fs
    if args.resample is not None:
        runner.resample = args.resample
    if args.resample_method is not None:
        runner.resample_method = args.resample_method
    if args.notch is not None:
        runner.notch = args.notch
    if args.anonymize:
        runner.anonymize = True
    if args.override is not None:
        runner.override = args.override

    for key in ("method", "threshold", "noise_cut", "thresholding",
                "remove_baseline", "protect_qrs"):
        value = getattr(args, f"emd_{key}")
        if value is not None:
            runner.emd_options[key] = value

    # Optional metadata CSV for .asc inputs
    def _has_asc(path):
        p = Path(path)
        if p.is_dir():
            return any(True for _ in p.rglob("*.asc"))
        return p.suffix.lower() == ".asc"

    if args.metadata:
        runner.metadata_csv = args.metadata
    elif _interactive() and _has_asc(args.input):
        use_meta = input("Do you want to provide metadata? [y/N]: ").strip().lower()
        if use_meta in ("y", "yes"):
            meta_path = input("Enter metadata CSV file path: ").strip()
            if meta_path:
                runner.metadata_csv = meta_path
            else:
                print("No metadata CSV path provided. Skipping metadata mapping.")

    # Announce the run
    from ecgproc.files import find_files
    from ecgproc.constants import SUPPORTED_EXTENSIONS
    files = find_files(Path(args.input), SUPPORTED_EXTENSIONS)
    if not files:
        print(f"No ECG files found in {args.input}")
        sys.exit(1)

    print(f"\nFound {len(files)} files to process.")
    print(f"Output directory: {runner.path_sink}")
    print(f"Output format: {runner.output_format}")
    if runner.pipeline:
        print(f"Processing pipeline: {' -> '.join(runner.pipeline)}")
        if 'notch' in runner.pipeline:
            print(f"Notch filter frequency: {runner.notch} Hz")
        if 'emd' in runner.pipeline:
            emd = runner.emd_options
            print(f"EMD: {emd['method']}, threshold {emd['threshold']} above {emd['noise_cut']} Hz, "
                  f"QRS protection {'on' if emd['protect_qrs'] else 'off'}, "
                  f"baseline {'removed' if emd['remove_baseline'] else 'kept'}")
    if runner.default_fs:
        print(f"Assumed sampling rate for files without one: {runner.default_fs} Hz")
    if runner.resample:
        print(f"Target resampling frequency: {runner.resample} Hz")
        print(f"Resampling method: {runner.resample_method}")
    print(f"Anonymize data: {'Yes' if runner.anonymize else 'No'}")
    print(f"Re-process existing outputs: {'Yes' if runner.override else 'No'}")
    print("-" * 30)

    bar = tqdm(total=len(files), desc="Processing ECG files", unit="file")

    def _progress(done, total, filename):
        bar.n = done
        bar.refresh()

    try:
        runner.run(progress_cb=_progress, log_cb=tqdm.write)
    finally:
        bar.close()

    print("\n### Summary ###")
    print(f"Processed successfully: {runner.stats['success']}")
    print(f"Skipped: {runner.stats['skipped']}")
    print(f"Failed to process: {runner.stats['failed']}")
    if runner.summary_path:
        print(f"Metadata: {runner.summary_path}")

    return 1 if runner.stats['failed'] and not runner.stats['success'] else 0
