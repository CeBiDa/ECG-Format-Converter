"""
ECG-Format-Converter — command line entry point.

Usage:
    python3 ecg_processor.py <input file/folder> --format <csv|xml|dcm|hl7|wfdb|mat|asc> --output <output_folder> --fs <original_fs> --pipeline <notch|bandpass|wavelet|emd> --resample <new_fs> --resample_method <fft|interpolation> --notch <50|60> --anonymize

Run without arguments for interactive prompts. All defaults live in config.ini.
"""
import os
import sys

# Add project root to sys.path
project_root = os.path.abspath(os.path.dirname(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ecgproc.cli import main

if __name__ == "__main__":
    sys.exit(main())
