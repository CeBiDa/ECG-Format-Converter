"""
Low-level input inspector: runs every supported file under a path through the
readers and prints what they see (leads, sampling rate, samples, metadata).
Nothing is written. Useful when a device export fails to convert.

Usage:
    python3 debug_ecg.py [file-or-folder]     # default: data/input_ecgs
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from ecgproc.constants import SUPPORTED_EXTENSIONS
from ecgproc.files import find_files
from ecgproc.readers import read_asc, read_csv, read_dat_hea, read_dcm, read_mat, read_xml

READERS = {
    ".mat": read_mat, ".dat": read_dat_hea, ".hea": read_dat_hea,
    ".csv": read_csv, ".asc": read_asc, ".dcm": read_dcm, ".xml": read_xml,
    ".hl7": read_xml,   # read_xml detects the AnnotatedECG schema itself
}

META_KEYS = ["source", "patient_id", "patient_firstname", "patient_lastname",
             "patient_birthdate", "patient_sex", "patient_age", "exam_date",
             "exam_time", "patient_weight", "patient_height", "pacemaker"]


def inspect(path):
    print(f"\n=== {path.name} ({path.stat().st_size / 1024:.1f} KB) ===")
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        print(f"  no reader for '{path.suffix}'")
        return False
    try:
        signals, meta = reader(path)
    except Exception as e:
        print(f"  READ FAILED: {e.__class__.__name__}: {e}")
        return False
    print(f"  shape: {signals.shape[0]} leads x {signals.shape[1]} samples")
    print(f"  fs: {meta.get('fs')}")
    print(f"  leads: {', '.join(str(s) for s in meta.get('signal_names', []))}")
    filled = {k: meta[k] for k in META_KEYS if meta.get(k) not in (None, "")}
    if filled:
        print("  metadata: " + ", ".join(f"{k}={v}" for k, v in filled.items()))
    else:
        print("  metadata: none found")
    return True


def main():
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else project_root / "data" / "input_ecgs"
    files = find_files(target, SUPPORTED_EXTENSIONS)
    if not files:
        print(f"No supported ECG files under {target}")
        return 1
    ok = sum(inspect(f) for f in files)
    print(f"\n{ok}/{len(files)} files readable.")
    return 0 if ok == len(files) else 1


if __name__ == "__main__":
    sys.exit(main())
