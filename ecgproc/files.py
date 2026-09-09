import re
import numpy as np
from pathlib import Path

def find_files(input_path, extensions):
    """
    Finds all relevant files in the given path with specified extensions.
    """
    input_path = Path(input_path)
    wanted = {ext.lower() for ext in extensions}
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in wanted else []

    # rglob is case-sensitive on Linux, so match the suffix ourselves: a
    # device that exports .MAT or .XML must be found the same way a single
    # file passed straight in is.
    all_files = sorted(f for f in input_path.rglob("*")
                       if f.is_file() and f.suffix.lower() in wanted)

    final_files_to_process = []
    processed_wfdb_stems = set()
    wfdb_extensions = {".dat", ".hea"}

    for file in all_files:
        if file.suffix.lower() in wfdb_extensions:
            # For WFDB, process one file per record. The record is the stem
            # within its own directory: two folders may each hold a record of
            # the same name, and both have to be processed.
            key = (file.parent, file.stem)
            if key not in processed_wfdb_stems:
                processed_wfdb_stems.add(key)
                final_files_to_process.append(file)
        else:
            # For all other formats, add them directly
            final_files_to_process.append(file)

    return final_files_to_process

def zero_pad(signal, target_len):
    if len(signal) > target_len:
        return signal[:target_len]
    padded = np.zeros(target_len)
    padded[:len(signal)] = signal
    return padded

def impute_missing(signal):
    if np.isnan(signal).any():
        nans = np.isnan(signal)
        not_nans = ~nans
        if not not_nans.any():
            # Nothing to interpolate from (a disconnected electrode arrives as
            # an all-NaN lead); np.interp would raise on the empty reference.
            return np.zeros_like(signal)
        signal[nans] = np.interp(np.flatnonzero(nans), np.flatnonzero(not_nans), signal[not_nans])
        signal[np.isnan(signal)] = 0
    return signal

def normalize_time(value: str) -> str:
    if not value:
        return ""
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 6:
        hh = digits[0:2]
        mm = digits[2:4]
        ss = digits[4:6]
        frac = digits[6:] if len(digits) > 6 else ""
        if frac:
            return f"{hh}{mm}{ss}.{frac}"
        else:
            return f"{hh}{mm}{ss}"
    return digits
