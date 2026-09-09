import traceback

import numpy as np
from tqdm import tqdm

from ecgproc import stats
from ecgproc.constants import FINAL_LEADS
from ecgproc.files import impute_missing
from ecgproc.leads import calculate_missing_leads
from ecgproc.preprocess import preprocess_signals, resample_signal
from ecgproc.readers import read_mat, read_dat_hea, read_csv, read_asc, read_dcm, read_xml
from ecgproc.writers import (write_csv, write_xml, write_dcm, write_hl7, write_mat,
                             write_wfdb, write_asc, wfdb_record_name)
from ecgproc.xml_to_dicom import create_dicom_file

# Process ECG file: read, preprocess, convert, and write output
#
# Returns a result dict:
#   {"status": "success" | "skipped" | "failed",
#    "file": input file name, "output": output path or None,
#    "signals": (12, n_samples) ndarray or None, "meta": dict or None,
#    "reason": short text for skipped/failed}
def process_file(file_path, output_dir, output_format, summary_list, pipeline, default_fs,
                 target_fs, notch_freq, resample_method, anonymize=False,
                 override=False, log=None, emd_options=None):
    _log = log if log is not None else tqdm.write
    def _result(status, output=None, signals_out=None, meta_out=None, reason=None):
        return {"status": status, "file": file_path.name, "output": output,
                "signals": signals_out, "meta": meta_out, "reason": reason}

    ext = file_path.suffix.lower()
    signals, meta = None, {}

    try:
        if ext == ".mat":
            signals, meta = read_mat(file_path)
        elif ext in [".dat", ".hea"]:
            signals, meta = read_dat_hea(file_path)
        elif ext == ".csv":
            signals, meta = read_csv(file_path)
        elif ext == ".asc":
            signals, meta = read_asc(file_path)
        elif ext == ".dcm":
            signals, meta = read_dcm(file_path)
        elif ext in (".xml", ".hl7"):
            # Detect if it is an HL7 file
            is_hl7 = ext == ".hl7"
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    header = f.read(1024)
                    if "AnnotatedECG" in header or "urn:hl7-org:v3" in header:
                        is_hl7 = True
            except OSError:
                pass

            # If output is DCM and it's NOT HL7, try the specific XMl to DCM converter first.
            # Only valid when no pre-processing/resampling is requested: the fast path
            # writes the raw waveform straight through.
            if output_format == "dcm" and not is_hl7 and not pipeline and not target_fs:
                try:
                    skipped_before = stats.count_skipped
                    signals, meta = create_dicom_file(str(file_path), str(output_dir), anonymize=anonymize, override=override)
                    if meta is not None:
                        out_file = output_dir / f"{file_path.stem}.{output_format}"
                        summary_entry = {
                            "filename": out_file.name,
                            "patient_id": meta.get("patient_id", ""),
                            "patient_firstname": meta.get("patient_firstname", ""),
                            "patient_lastname": meta.get("patient_lastname", ""),
                            "patient_birthdate": meta.get("patient_birthdate", ""),
                            "n_leads": len(meta.get("signal_names", [])),
                            "n_samples": meta.get("n_samples", ""),
                            "sampling_freq": meta.get("fs") or meta.get("sampling_freq", ""),
                            "signal_names": ",".join(meta.get("signal_names", [])),
                            "patient_sex": meta.get("patient_sex", ""),
                            "patient_weight": meta.get("patient_weight", ""),
                            "patient_height": meta.get("patient_height", ""),
                            "exam_date": meta.get("exam_date", ""),
                            "exam_time": meta.get("exam_time", ""),
                            "pacemaker": meta.get("pacemaker", ""),
                            "patient_age": meta.get("patient_age", ""),
                            "original_leads": ",".join(meta.get("original_leads", [])) if meta.get("original_leads") else "",
                            "calculated_leads": ",".join(meta.get("calculated_leads", [])) if meta.get("calculated_leads") else "",
                            "pon": meta.get("pon", ""),
                            "poff": meta.get("poff", ""),
                            "pdur": meta.get("pdur", ""),
                        }
                        summary_list.append(summary_entry)
                        # create_dicom_file stacks samples as (n_samples, n_leads)
                        sig_out = signals.T if signals is not None and signals.ndim == 2 else signals
                        return _result("success", output=str(out_file), signals_out=sig_out, meta_out=meta)
                    if stats.count_skipped > skipped_before:
                        return _result("skipped", reason="output exists (use override to re-process)")
                    return _result("failed", reason="XML to DICOM conversion failed")
                except Exception:
                    # Fallback to standard read if create_dicom_file fails
                    pass

            # Standard read (handles both HL7 and other XMLs)
            signals, meta = read_xml(file_path)
        else:
            stats.count_skipped += 1
            return _result("skipped", reason=f"unsupported extension '{ext}'")
    except NotImplementedError:
        stats.count_skipped += 1
        _log(f"Skipping {file_path.name}: Reader not implemented for this format combination.")
        return _result("skipped", reason="reader not implemented")
    except Exception as e:
        stats.count_failed += 1
        # Everything the caller learns has to come through _log: printing a
        # traceback here goes to a stderr the GUI never shows.
        _log(f"Error reading {file_path.name}: {e}")
        _log(traceback.format_exc().rstrip())
        return _result("failed", reason=f"read error: {e}")

    if signals is not None:
        # Apply anonymization by wiping IDs and Names
        if anonymize:
            meta["patient_id"] = ""
            meta["patient_firstname"] = ""
            meta["patient_lastname"] = ""
            meta["patient_birthdate"] = ""

        # Assign standard lead names for 12-lead MAT files
        if meta.get("source") == "mat" and signals.shape[0] == 12:
            meta["signal_names"] = FINAL_LEADS.copy()

        fs = meta.get("fs") or default_fs
        # Persist the resolved rate: the writers and the summary read meta,
        # not this local, and a file that stores none still has to carry the
        # rate the user asserted with --fs / default_fs.
        if fs:
            meta["fs"] = fs
        if pipeline or target_fs:
            if not fs:
                _log(f"Skipping pre-processing for {file_path.name}: Unknown sampling frequency (set a fallback rate / --fs).")
            else:
                # Apply pre-processing pipeline
                if pipeline:
                    if 'emd' in pipeline:
                        _log(f"Processing {file_path.name} with EMD (this may take a while)...")
                    try:
                        signals = preprocess_signals(signals, pipeline, fs, notch_freq,
                                                     emd_options=emd_options, log=_log)
                    except Exception as e:
                        stats.count_failed += 1
                        _log(f"Error pre-processing {file_path.name}: {e}")
                        return _result("failed", reason=f"pre-processing error: {e}")

                # Resample signals if a target frequency is provided
                if target_fs and target_fs != fs:
                    try:
                        signals = resample_signal(signals, original_fs=fs, target_fs=target_fs, method=resample_method)
                        meta['fs'] = target_fs # Update metadata with new sampling frequency
                        meta['n_samples'] = signals.shape[1]
                    except Exception as e:
                        stats.count_failed += 1
                        _log(f"Error resampling {file_path.name}: {e}")
                        return _result("failed", reason=f"resampling error: {e}")


        signals = np.array([impute_missing(lead) for lead in signals])

        orig_signal_names = meta.get("signal_names", [])
        meta["original_leads"] = orig_signal_names.copy()
        signals, updated_names, calculated = calculate_missing_leads(signals, orig_signal_names.copy())
        meta["signal_names"] = updated_names
        meta["calculated_leads"] = calculated

        # Reorder to FINAL_LEADS
        lead_map = {name.upper(): i for i, name in enumerate(updated_names)}
        reordered = []
        missing_final = []
        for ld in FINAL_LEADS:
            if ld.upper() in lead_map:
                reordered.append(signals[lead_map[ld.upper()]])
            else:
                reordered.append(np.zeros(signals.shape[1]))
                missing_final.append(ld)
        signals = np.vstack(reordered)
        meta["signal_names"] = FINAL_LEADS
        meta["missing_leads"] = missing_final

        if output_format == "wfdb" and not fs:
            stats.count_skipped += 1
            _log(f"Skipping {file_path.name}: WFDB output needs a known sampling "
                 "frequency (set a fallback rate / --fs).")
            return _result("skipped", reason="WFDB output requires a sampling frequency")

        if output_format == "wfdb":
            # A WFDB record is a .hea header plus a .dat sample file. The header
            # stands in for the record wherever one output path is expected.
            record_name = wfdb_record_name(file_path.stem)
            if record_name != file_path.stem:
                _log(f"{file_path.name}: WFDB record written as '{record_name}' "
                     "(record names take only letters, digits, '-' and '_').")
            out_file = output_dir / f"{record_name}.hea"
        else:
            out_file = output_dir / f"{file_path.stem}.{output_format}"

        if out_file.exists() and not override:
            stats.count_skipped += 1
            return _result("skipped", reason="output exists (use override to re-process)")

        try:
            if output_format == "csv":
                write_csv(out_file, signals, meta)
            elif output_format == "xml":
                write_xml(out_file, signals, meta)
            elif output_format == "dcm":
                write_dcm(out_file, signals, meta)
            elif output_format == "hl7":
                write_hl7(out_file, signals, meta)
            elif output_format == "mat":
                write_mat(out_file, signals, meta)
            elif output_format == "wfdb":
                write_wfdb(str(out_file), signals, meta)
            elif output_format == "asc":
                write_asc(out_file, signals, meta)
            else:
                stats.count_skipped += 1
                _log(f"Skipping write for {out_file.name}: No writer implemented for format '{output_format}'.")
                return _result("skipped", reason=f"no writer for '{output_format}'")

            stats.count_success += 1
        except Exception as e:
            stats.count_failed += 1
            _log(f"Error writing {out_file.name}: {e}")
            return _result("failed", reason=f"write error: {e}")

        summary_entry = {
            "filename": out_file.name,
            "patient_id": meta.get("patient_id", ""),
            "patient_firstname": meta.get("patient_firstname", ""),
            "patient_lastname": meta.get("patient_lastname", ""),
            "patient_birthdate": meta.get("patient_birthdate", ""),
            "n_leads": signals.shape[0],
            "n_samples": meta.get("n_samples", signals.shape[1]),
            "sampling_freq": meta.get("fs") or meta.get("sampling_freq", ""),
            "signal_names": ",".join(meta.get("signal_names", [])) if meta.get("signal_names") else "",
            "patient_sex": meta.get("patient_sex", ""),
            "patient_age": meta.get("patient_age", ""),
            "patient_weight": meta.get("patient_weight", ""),
            "patient_height": meta.get("patient_height", ""),
            "exam_date": meta.get("exam_date", ""),
            "exam_time": meta.get("exam_time", ""),
            "pacemaker": meta.get("pacemaker", ""),
            "original_leads": ",".join(meta.get("original_leads", [])),
            "calculated_leads": ",".join(meta.get("calculated_leads", [])),
            "pon": meta.get("pon", ""),
            "poff": meta.get("poff", ""),
            "pdur": meta.get("pdur", ""),
        }
        summary_list.append(summary_entry)
        return _result("success", output=str(out_file), signals_out=signals, meta_out=meta)

    stats.count_failed += 1
    return _result("failed", reason="reader returned no signals")