import os
import re
from datetime import datetime

import numpy as np
import pandas as pd
import xml.etree.ElementTree as ET
import wfdb

import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, generate_uid, PYDICOM_IMPLEMENTATION_UID

from ecgproc.constants import FINAL_LEADS

# WFDB and DICOM both store int16 samples
INT16_MAX = 32767
INT16_MIN = -32768


def _text(value):
    """Metadata as XML text: never None, never a date/int that ET refuses."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "strftime"):
        return value.isoformat()
    return str(value)


def _lead_names(meta, n_leads):
    """Lead names for n_leads rows: the metadata's, only if it has the right count."""
    names = list(meta.get("signal_names") or [])
    if len(names) != n_leads:
        names = [f"lead{i + 1}" for i in range(n_leads)]
    return [_text(name) for name in names]


def _to_int16(signals):
    """
    Quantise to the int16 grid DICOM stores. Clipping is the point: an
    unclipped astype() wraps, so a 40 mV spike comes back as a deep negative
    one, and NaN lands on an arbitrary sample value.
    """
    arr = np.nan_to_num(np.asarray(signals, dtype=np.float64),
                        nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(np.round(arr), INT16_MIN, INT16_MAX).astype(np.int16)

# CSV Writer
def write_csv(output_path, signals, meta):
    df = pd.DataFrame(signals.T, columns=_lead_names(meta, signals.shape[0]))
    df.to_csv(output_path, index=False)

# XML Writer
def write_xml(output_path, signals, meta):
    root = ET.Element("ECG")
    # Metadata section
    md = ET.SubElement(root, "metadata")
    for tag in ("source", "patient_id", "patient_firstname", "patient_lastname",
                "patient_birthdate", "patient_sex", "patient_age", "patient_weight",
                "patient_height", "exam_date", "exam_time", "pacemaker",
                "pon", "poff", "pdur"):
        ET.SubElement(md, tag).text = _text(meta.get(tag))
    ET.SubElement(md, "n_leads").text = str(signals.shape[0])
    ET.SubElement(md, "n_samples").text = str(signals.shape[1])
    ET.SubElement(md, "sampling_freq").text = _text(meta.get("fs") or meta.get("sampling_freq", ""))
    for tag in ("original_leads", "calculated_leads", "missing_leads"):
        ET.SubElement(md, tag).text = ",".join(_text(v) for v in (meta.get(tag) or []))

    # Waveforms section. Normal runs always hand over the full 12; a caller
    # using the writer directly may not, and must not get an IndexError.
    for i, lead_name in enumerate(FINAL_LEADS):
        if i >= signals.shape[0]:
            break
        lead_el = ET.SubElement(root, "lead", name=lead_name)
        wf = ET.SubElement(lead_el, "waveform")
        wf.text = ",".join(map(str, signals[i]))

    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="\t", level=0)
    except AttributeError:
        pass
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

# DICOM Writer
def write_dcm(output_path, signals, meta):
    def _digits(s):
        return "" if not s else "".join(ch for ch in str(s) if ch.isdigit())

    def _normalize_date(value):
        d = _digits(value)
        if len(d) >= 8:
            yyyy = d[:4]
            mmdd = d[4:8]
            if 1900 <= int(yyyy) <= 2100:
                return yyyy + mmdd
            dd = d[:2]; mm = d[2:4]; yyyy = d[4:8]
            return f"{yyyy}{mm}{dd}"
        return d

    def _normalize_time(value):
        if not value:
            return ""
        digits = _digits(value)
        if len(digits) < 6:
            return digits
        hh, mm, ss = digits[0:2], digits[2:4], digits[4:6]
        frac = digits[6:]
        return f"{hh}{mm}{ss}" + (f".{frac}" if frac else "")

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.9.1.1'
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(output_path), {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    # A source that carried no UID leaves an empty string in meta, and .get()
    # does not fall back on that -- an empty StudyInstanceUID is not a valid
    # DICOM instance, so generate one.
    ds.StudyInstanceUID  = meta.get("study_uid") or generate_uid()
    ds.SeriesInstanceUID = meta.get("series_uid") or generate_uid()
    ds.SeriesNumber   = int(meta.get("series_number") or 1)
    ds.InstanceNumber = int(meta.get("instance_number") or 1)
    ds.Modality = "ECG"
    ds.PatientID = _text(meta.get("patient_id", "unknown"))
    firstname = _text(meta.get("patient_firstname"))
    lastname = _text(meta.get("patient_lastname"))
    if firstname or lastname:
        ds.PatientName = f"{lastname}^{firstname}".strip('^')
    else:
        ds.PatientName = ""  
    ds.PatientSex  = _text(meta.get("patient_sex"))

    patient_birthdate = _text(meta.get("patient_birthdate"))
    if patient_birthdate:
        ds.PatientBirthDate = _normalize_date(patient_birthdate)
    else:
        ds.PatientBirthDate = ""

    patient_age = meta.get("patient_age")
    if patient_age:
        try:
            age_str = f"{int(float(patient_age)):03d}Y"
            ds.PatientAge = age_str
        except (ValueError, TypeError):
            pass

    ds.StudyDate = _normalize_date(_text(meta.get("exam_date")))
    ds.StudyTime = _normalize_time(_text(meta.get("exam_time")))
    ds.ContentDate = ds.StudyDate
    ds.AcquisitionDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime
    ds.AcquisitionTime = ds.StudyTime
    ds.InstanceCreationDate = datetime.now().strftime("%Y%m%d")
    ds.InstanceCreationTime = datetime.now().strftime("%H%M%S")

    if signals.ndim != 2:
        raise ValueError("signals must be 2D: (leads, samples)")
    if signals.shape[0] > signals.shape[1] and signals.shape[1] <= 16:
        signals = signals.T
    n_leads, n_samples = int(signals.shape[0]), int(signals.shape[1])

    wf = Dataset()
    wf.WaveformOriginality = "ORIGINAL"
    wf.NumberOfWaveformChannels = n_leads
    wf.NumberOfWaveformSamples = n_samples
    wf.SamplingFrequency = meta.get("fs") or meta.get("sampling_freq", 500)
    wf.WaveformBitsAllocated = 16
    wf.WaveformBitsStored = 16
    wf.HighBit = 15
    wf.WaveformSampleInterpretation = "SS"
    wf.ChannelDefinitionSequence = []

    # One channel definition per channel: a name list of the wrong length
    # would leave NumberOfWaveformChannels disagreeing with the sequence.
    signal_names = _lead_names(meta, n_leads)

    # Channel definitions
    for i, lead in enumerate(signal_names):
        ch = Dataset()
        ch.ChannelSensitivity = 1.0
        ch.ChannelSensitivityUnitsSequence = [Dataset()]
        ch.ChannelSensitivityUnitsSequence[0].CodeValue = "uV"
        ch.ChannelSensitivityUnitsSequence[0].CodingSchemeDesignator = "UCUM"
        ch.ChannelSensitivityUnitsSequence[0].CodeMeaning = "microvolt"
        ch.ChannelLabel = str(lead)
        ch.ChannelSourceSequence = [Dataset()]
        ch.ChannelSourceSequence[0].CodeValue = str(i + 1)
        ch.ChannelSourceSequence[0].CodingSchemeDesignator = "PYDICOM"
        ch.ChannelSourceSequence[0].CodeMeaning = str(lead)
        wf.ChannelDefinitionSequence.append(ch)

    # Scale signals to uV if needed
    if signals.size > 0 and np.max(np.abs(signals)) < 30.0:
        signals = signals * 1000.0

    # Waveform (time-interleaved)
    wf.WaveformData = np.ascontiguousarray(_to_int16(signals.T)).tobytes()
    ds.WaveformSequence = Sequence([wf])

    # Add annotations for pon, poff, pdur
    annotations = []
    pon = meta.get("pon")
    poff = meta.get("poff")
    pdur = meta.get("pdur")

    if pon is not None:
        ann = Dataset()
        ann.ConceptNameCodeSequence = [Dataset()]
        ann.ConceptNameCodeSequence[0].CodeValue = "P-ON"
        ann.ConceptNameCodeSequence[0].CodingSchemeDesignator = "LOCAL"
        ann.ConceptNameCodeSequence[0].CodeMeaning = "P-wave onset"
        ann.NumericValue = str(pon)
        annotations.append(ann)

    if poff is not None:
        ann = Dataset()
        ann.ConceptNameCodeSequence = [Dataset()]
        ann.ConceptNameCodeSequence[0].CodeValue = "P-OFF"
        ann.ConceptNameCodeSequence[0].CodingSchemeDesignator = "LOCAL"
        ann.ConceptNameCodeSequence[0].CodeMeaning = "P-wave offset"
        ann.NumericValue = str(poff)
        annotations.append(ann)

    if pdur is not None:
        ann = Dataset()
        ann.ConceptNameCodeSequence = [Dataset()]
        ann.ConceptNameCodeSequence[0].CodeValue = "P-DUR"
        ann.ConceptNameCodeSequence[0].CodingSchemeDesignator = "LOCAL"
        ann.ConceptNameCodeSequence[0].CodeMeaning = "P-wave duration"
        ann.NumericValue = str(pdur)
        annotations.append(ann)

    if annotations:
        ds.WaveformAnnotationSequence = Sequence(annotations)

    ds.save_as(str(output_path), write_like_original=False)

# HL7 Writer
def write_hl7(output_path, signals, meta):
    ET.register_namespace('xsi', "http://www.w3.org/2001/XMLSchema-instance")
    ns = {
        "xmlns": "urn:hl7-org:v3",
        "xsi": "http://www.w3.org/2001/XMLSchema-instance"
    }
    def set_type(elem, type_name):
        elem.set(f"{{{ns['xsi']}}}type", type_name)

    if signals.size > 0 and np.max(np.abs(signals)) < 30.0:
        signals = signals * 1000.0
    signals = np.nan_to_num(signals).astype(int)
    # Note: xsi is declared automatically by ElementTree because set_type() writes
    # namespaced attributes; declaring it here as well produced a duplicate
    # xmlns:xsi attribute that strict parsers reject.
    root = ET.Element("AnnotatedECG", attrib={"xmlns": ns["xmlns"]})

    # Patient Information
    patient = ET.SubElement(root, "patient")
    # XML attributes take strings only: a None or numeric id from a MAT file
    # would fail at serialisation time, after the waveform work was done.
    ET.SubElement(patient, "id", extension=_text(meta.get("patient_id", "unknown")))
    patient_person = ET.SubElement(patient, "patientPerson")
    firstname = _text(meta.get("patient_firstname"))
    lastname = _text(meta.get("patient_lastname"))
    if firstname or lastname:
        name_node = ET.SubElement(patient_person, "name")
        if lastname:
            ET.SubElement(name_node, "family").text = lastname
        if firstname:
            ET.SubElement(name_node, "given").text = firstname

    ET.SubElement(patient_person, "administrativeGenderCode", code=_text(meta.get("patient_sex")))

    
    # Mapping Birthdate / Age
    birthdate = _text(meta.get("patient_birthdate"))
    patient_age = meta.get("patient_age")    

    if birthdate or patient_age:
        birth_time = ET.SubElement(patient_person, "birthTime")
        if birthdate:
            birth_time.set("value", re.sub(r'\D', '', str(birthdate)))
        if patient_age:
            try:
                age_val = str(int(float(patient_age)))
                ET.SubElement(birth_time, "age", value=age_val, unit="a")
            except (ValueError, TypeError):
                pass

    # ECG Series
    component = ET.SubElement(root, "component")
    series = ET.SubElement(component, "series")

    # Date/Time information
    effective_time = ET.SubElement(series, "effectiveTime")
    exam_date = re.sub(r'\D', '', _text(meta.get('exam_date')))
    exam_time = re.sub(r'\D', '', _text(meta.get('exam_time')))
    if not exam_date: 
        full_time = datetime.now().strftime("%Y%m%d%H%M%S")
    else:
        full_time = f"{exam_date}{exam_time}"
        
    ET.SubElement(effective_time, "low", value=full_time)

    # Waveform data
    component2 = ET.SubElement(series, "component")
    sequence_set = ET.SubElement(component2, "sequenceSet")
    
    # Sampling frequency information
    fs = float(meta.get("fs") or meta.get("sampling_freq", 500.0))
    period_s = 1.0 / fs # Period in seconds
    
    seq_time = ET.SubElement(sequence_set, "sequence")
    ET.SubElement(seq_time, "code", code="TIME_RELATIVE", codeSystem="2.16.840.1.113883.5.4")
    
    val_time = ET.SubElement(seq_time, "value")
    set_type(val_time, "GLIST_TS")
    
    ET.SubElement(val_time, "head", value="0", unit="s")
    ET.SubElement(val_time, "increment", value=f"{period_s:.6f}", unit="s")

    for i, lead_name in enumerate(FINAL_LEADS):
        if i >= signals.shape[0]: break
        
        seq_lead = ET.SubElement(sequence_set, "sequence")
        
        lead_code = f"MDC_ECG_LEAD_{lead_name.upper()}"
        ET.SubElement(seq_lead, "code", code=lead_code, codeSystem="2.16.840.1.113883.6.24")
        
        val_lead = ET.SubElement(seq_lead, "value")
        set_type(val_lead, "SLIST_PQ")
        
        # Define Scale
        ET.SubElement(val_lead, "origin", value="0", unit="uV")
        ET.SubElement(val_lead, "scale", value="1", unit="uV")
        
        digits = ET.SubElement(val_lead, "digits")
        digits.text = " ".join(map(str, signals[i]))

    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="\t", level=0)
    except AttributeError:
        pass
    tree.write(output_path, encoding='UTF-8', xml_declaration=True)

# MAT Writer
def write_mat(output_path, signals, meta):
    import scipy.io as sio
    mat_data = {
        'val': signals,
        'fs': meta.get('fs'),
        'study_id': meta.get('patient_id'),
        'age': meta.get('patient_age'),
        'sex': meta.get('patient_sex'),
        'pon': meta.get('pon'),
        'poff': meta.get('poff'),
        'pdur': meta.get('pdur')
    }
    # savemat cannot serialise None, and an empty string lands as a 0-length
    # char array that reads back as the literal "[]". Sources that do not carry
    # a field leave it as one or the other, so drop both: the key set differs
    # per record rather than carrying junk.
    mat_data = {k: v for k, v in mat_data.items()
                if v is not None and not (isinstance(v, str) and not v.strip())}
    sio.savemat(output_path, mat_data)

# WFDB Writer
def wfdb_record_name(stem):
    """
    Reduce a file stem to WFDB's record-name grammar. wrsamp rejects '.'
    outright, and a name carrying spaces or punctuation writes a header that
    wfdb.rdrecord then refuses to parse back, so keep to letters, digits,
    '-' and '_'.
    """
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(stem)) or "record"


def write_wfdb(output_path, signals, meta):
    """
    Write a WFDB record pair: <stem>.hea header + <stem>.dat samples. The
    extension of output_path is ignored; only its stem and directory are used.
    """
    signals = np.nan_to_num(np.asarray(signals, dtype=np.float64),
                            nan=0.0, posinf=0.0, neginf=0.0)
    fs = meta.get('fs')
    if not fs:
        raise ValueError("WFDB output requires a known sampling frequency "
                         "(set a fallback rate / --fs).")
    n_sig = signals.shape[0]
    sig_name = _lead_names(meta, n_sig)

    # wfdb.wrsamp rejects path separators inside record_name -> split into stem + write_dir
    base = os.path.splitext(str(output_path))[0]
    record_name = wfdb_record_name(os.path.basename(base))
    write_dir = os.path.dirname(base) or "."
    os.makedirs(write_dir, exist_ok=True)

    # Same amplitude heuristic as the DICOM and HL7 writers: a peak below 30
    # means the samples are millivolts. One ADC unit is 1 uV either way.
    peak = float(np.max(np.abs(signals))) if signals.size else 0.0
    units, gain = ("mV", 1000.0) if 0 < peak < 30.0 else ("uV", 1.0)
    if peak * gain > INT16_MAX:
        gain = INT16_MAX / peak

    # Quantise here and hand wrsamp an explicit fmt/gain/baseline. Left to
    # derive them itself it calls est_res(), which raises on a channel of
    # constant value -- and leads that were neither recorded nor derivable
    # arrive here zero-filled.
    d_signal = np.clip(np.round(signals.T * gain), -INT16_MAX, INT16_MAX)
    wfdb.wrsamp(record_name=record_name, fs=fs, p_signal=d_signal / gain,
                sig_name=sig_name, units=[units] * n_sig,
                fmt=["16"] * n_sig, adc_gain=[gain] * n_sig,
                baseline=[0] * n_sig, write_dir=write_dir)

# ASC Writer
def write_asc(output_path, signals, meta):
    df = pd.DataFrame(signals.T, columns=_lead_names(meta, signals.shape[0]))
    df.to_csv(output_path, index=False)