import re
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import wfdb
import pydicom
import scipy.io as sio
from scipy.signal import resample


from ecgproc.xml_utils import parse_xml, find_ecg_rhythms, extract_rhythms_snippet, parse_samples, find_first_tag_text, detect_sampling_frequency, lxml_etree
from ecgproc.constants import FINAL_LEADS
from ecgproc.files import zero_pad

# MAT Reader
def read_mat(file_path):
    mat = sio.loadmat(file_path, struct_as_record=False, squeeze_me=True)
    signals = None
    if "template" in mat:
        signals = mat["template"]
    elif "val" in mat:
        signals = mat["val"]
    elif "ecg" in mat:
        signals = mat["ecg"]

    if signals is None:
        raise ValueError(f"No ECG signals found in {file_path} (checked for 'template', 'val', 'ecg')")

    def _get_scalar(data, key):
        if key in data:
            item = data[key]
            if hasattr(item, 'value'):
                return item.value
            return item
        return None

    # Signals (n_leads, n_samples)
    if signals.shape[0] > signals.shape[1] and signals.shape[1] <= 16:
        signals = signals.T

    n_leads = signals.shape[0]

    # Create signal names
    if n_leads == 12:
        signal_names = FINAL_LEADS.copy()
    else:
        signal_names = [f"lead{i+1}" for i in range(n_leads)]

    # Extract metadata fields
    fs = _get_scalar(mat, 'fs')
    patient_id = _get_scalar(mat, 'study_id')
    age = _get_scalar(mat, 'age')
    sex = _get_scalar(mat, 'sex')
    pon = _get_scalar(mat, 'pon')
    poff = _get_scalar(mat, 'poff')
    pdur = _get_scalar(mat, 'pdur')

    meta = {
        "source": "mat",
        "signal_names": signal_names,
        "n_samples": signals.shape[1],
        "fs": fs,
        "patient_id": str(patient_id) if patient_id is not None else "",
        "patient_firstname": "",
        "patient_lastname": "",
        "patient_birthdate": "",
        "patient_age": age,
        "patient_sex": str(sex) if sex is not None else "",
        "exam_date": "",
        "exam_time": "",
        "pon": pon,
        "poff": poff,
        "pdur": pdur
    }
    return signals, meta

# WFDB Reader
def read_dat_hea(file_path):
    # Strip only the trailing .dat/.hea: a blanket str.replace also eats the
    # same text out of a parent directory name ("/data.set/rec.hea").
    file_path = Path(file_path)
    record_name = str(file_path.with_suffix("")) if file_path.suffix.lower() in (".dat", ".hea") \
        else str(file_path)
    record = wfdb.rdrecord(record_name)
    if record.p_signal is None:
        raise ValueError(f"WFDB record {record_name} carries no sample data.")
    signals = record.p_signal.T

    # base_date/base_time are datetime objects; every writer and the summary
    # expect plain text, and ElementTree refuses to serialise anything else.
    def _stamp(value, fmt):
        if value in (None, ""):
            return ""
        return value.strftime(fmt) if hasattr(value, "strftime") else str(value)

    meta = {
        "source": "wfdb",
        "fs": record.fs,
        "signal_names": list(record.sig_name or []),
        "n_samples": signals.shape[1],
        "patient_id": str(getattr(record, "patient_id", "") or ""),
        "patient_firstname": "",
        "patient_lastname": "",
        "patient_birthdate": "",
        "exam_date": _stamp(getattr(record, "base_date", ""), "%Y%m%d"),
        "exam_time": _stamp(getattr(record, "base_time", ""), "%H%M%S"),
    }
    return signals, meta

# CSV Reader
def read_csv(file_path):
    df = pd.read_csv(file_path)
    meta = {
        "source": "csv",
        "signal_names": list(df.columns),
        "n_samples": df.shape[0],
        "fs": None,
        "patient_id": "",
        "patient_firstname": "",
        "patient_lastname": "",
        "patient_birthdate": "",
        "exam_date": "",
        "exam_time": ""
    }
    return df.values.T, meta

# ASC Reader
def read_asc(file_path):
    df = pd.read_csv(file_path)
    signals = df.values.T # shape (n_leads, n_samples)
    meta = {
        "source": "asc",
        "signal_names": list(df.columns),
        "n_samples": signals.shape[1],
        "fs": None,
        "patient_id": "",
        "patient_firstname": "",
        "patient_lastname": "",
        "patient_birthdate": "",
        "exam_date": "",
        "exam_time": ""
    }
    return signals, meta

# DICOM Reader
def read_dcm(file_path):
    ds = pydicom.dcmread(file_path)
    
    # Parse Name
    patient_name = getattr(ds, "PatientName", "")
    firstname = ""
    lastname = ""
    if patient_name:
        if hasattr(patient_name, "given_name"):
            firstname = str(patient_name.given_name)
        if hasattr(patient_name, "family_name"):
            lastname = str(patient_name.family_name)
        else:
            parts = str(patient_name).split('^')
            if len(parts) > 0: lastname = parts[0]
            if len(parts) > 1: firstname = parts[1]

    # Extract Date
    def _get_exam_date(dataset):
        if "AcquisitionDateTime" in dataset:
            dt = str(dataset.AcquisitionDateTime)
            if len(dt) >= 8: return dt[:8]
            
        for tag in ["AcquisitionDate", "ContentDate", "SeriesDate", "StudyDate"]:
            if tag in dataset:
                val = str(getattr(dataset, tag))
                if val: return val[:8]
        return ""

    # Extract Time
    def _get_exam_time(dataset):
        if "AcquisitionDateTime" in dataset:
            dt = str(dataset.AcquisitionDateTime)
            if len(dt) >= 14: return dt[8:14]
            
        for tag in ["AcquisitionTime", "ContentTime", "SeriesTime", "StudyTime"]:
            if tag in dataset:
                val = str(getattr(dataset, tag))
                if val: return val.split('.')[0][:6]
        return ""

    meta = {
        "source": "dcm",
        "patient_id": str(getattr(ds, "PatientID", "")),
        "patient_firstname": firstname,
        "patient_lastname": lastname,
        "patient_birthdate": str(getattr(ds, "PatientBirthDate", "")),
        "patient_sex": str(getattr(ds, "PatientSex", "")),
        "patient_age": str(getattr(ds, "PatientAge", "")),
        "patient_weight": str(getattr(ds, "PatientWeight", "")),
        "patient_height": str(getattr(ds, "PatientSize", "")),
        "exam_date": _get_exam_date(ds),
        "exam_time": _get_exam_time(ds),
        "study_uid": str(getattr(ds, "StudyInstanceUID", "")),
        "series_uid": str(getattr(ds, "SeriesInstanceUID", "")),
    }
    
    # Extract Waveform Data 
    if not hasattr(ds, "WaveformSequence"):
        raise ValueError(f"DICOM file {file_path} does not contain a WaveformSequence.")

    best_data = None
    best_signal_names = []
    max_samples = -1
    best_fs = None

    for wf in ds.WaveformSequence:
        waveform_data = getattr(wf, "WaveformData", None)
        if waveform_data is None and (0x5400, 0x1010) in wf:
            waveform_data = wf[0x5400, 0x1010].value
            
        if not waveform_data:
            continue

        num_channels = getattr(wf, "NumberOfWaveformChannels", 0)
        if num_channels == 0 and (0x003a, 0x0005) in wf:
            num_channels = wf[0x003a, 0x0005].value
            
        if num_channels <= 0:
            continue

        # WaveformSampleInterpretation says whether the samples are signed;
        # reading unsigned data as int16 turns everything above half-scale
        # negative. SS/SB are signed, US/UB unsigned (MB/AB are companded 8-bit).
        bits_allocated = getattr(wf, "WaveformBitsAllocated", 16)
        interpretation = str(getattr(wf, "WaveformSampleInterpretation", "SS")).strip().upper()
        signed = interpretation not in ("US", "UB")
        if bits_allocated == 8:
            dtype = np.int8 if signed else np.uint8
        elif bits_allocated == 32:
            dtype = np.int32 if signed else np.uint32
        else:
            dtype = np.int16 if signed else np.uint16

        data = np.frombuffer(waveform_data, dtype=dtype)
        
        remainder = len(data) % num_channels
        if remainder != 0:
            data = data[:-remainder] 
            
        try:
            data = data.reshape(-1, num_channels).T
        except ValueError:
            continue 

        # Keep the block with the longest duration
        if data.shape[1] > max_samples:
            max_samples = data.shape[1]
            best_data = data
            # pydicom hands back a DSfloat here. It behaves like a float, but
            # scipy.io.savemat stores the object's attributes instead of the
            # number, so keep meta["fs"] a plain float for the writers.
            best_fs = getattr(wf, "SamplingFrequency", None)
            if best_fs is not None:
                try:
                    best_fs = float(best_fs)
                except (TypeError, ValueError):
                    best_fs = None
            
            signal_names = []
            channel_defs = getattr(wf, "ChannelDefinitionSequence", [])
            for i, ch_def in enumerate(channel_defs):
                name = f"lead_{i+1}"
                if hasattr(ch_def, "ChannelSourceSequence") and len(ch_def.ChannelSourceSequence) > 0:
                    if hasattr(ch_def.ChannelSourceSequence[0], "CodeMeaning"):
                        name = ch_def.ChannelSourceSequence[0].CodeMeaning
                elif hasattr(ch_def, "ChannelLabel"):
                    name = ch_def.ChannelLabel
                signal_names.append(name)
            best_signal_names = signal_names

    # Final check
    if best_data is None:
        raise ValueError(f"No valid continuous rhythm data could be extracted")

    # Apply to meta
    meta["fs"] = best_fs
    meta["n_samples"] = best_data.shape[1]
    meta["signal_names"] = best_signal_names
    signals = best_data

    return signals, meta

# HL7 aECG Reader
def read_hl7_aecg(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    
    # Find tags
    def get_local_tag(tag):
        return tag.split('}')[-1] if '}' in tag else tag

    def find_child_by_local_name(parent, local_name):
        for child in parent:
            if get_local_tag(child.tag) == local_name:
                return child
        return None

    # Extract demographics
    patient_id = ""
    patient_firstname = ""
    patient_lastname = ""
    patient_birthdate = ""
    gender = ""
    
    for trial_subject in root.iter():
        if get_local_tag(trial_subject.tag) == "trialSubject":
            id_node = find_child_by_local_name(trial_subject, "id")
            if id_node is not None:
                patient_id = id_node.get("extension", "")
            
            person_node = find_child_by_local_name(trial_subject, "subjectDemographicPerson")
            if person_node is not None:
                gender_node = find_child_by_local_name(person_node, "administrativeGenderCode")
                if gender_node is not None:
                    gender = gender_node.get("code", "")
                
                name_node = find_child_by_local_name(person_node, "name")
                if name_node is not None:
                    given_node = find_child_by_local_name(name_node, "given")
                    if given_node is not None and given_node.text:
                        patient_firstname = given_node.text.strip()
                    family_node = find_child_by_local_name(name_node, "family")
                    if family_node is not None and family_node.text:
                        patient_lastname = family_node.text.strip()
                
                # Check for birthTime
                birth_node = find_child_by_local_name(person_node, "birthTime")
                if birth_node is not None:
                    patient_birthdate = birth_node.get("value", "")
    
            break

    # Fallback: files written by this tool carry a simplified
    # patient/patientPerson block instead of a trialSubject.
    if not patient_id and not patient_lastname:
        for patient_node in root.iter():
            if get_local_tag(patient_node.tag) != "patient":
                continue
            id_node = find_child_by_local_name(patient_node, "id")
            if id_node is not None:
                patient_id = id_node.get("extension", "")
            person_node = find_child_by_local_name(patient_node, "patientPerson")
            if person_node is not None:
                gender_node = find_child_by_local_name(person_node, "administrativeGenderCode")
                if gender_node is not None:
                    gender = gender_node.get("code", "")
                name_node = find_child_by_local_name(person_node, "name")
                if name_node is not None:
                    given_node = find_child_by_local_name(name_node, "given")
                    if given_node is not None and given_node.text:
                        patient_firstname = given_node.text.strip()
                    family_node = find_child_by_local_name(name_node, "family")
                    if family_node is not None and family_node.text:
                        patient_lastname = family_node.text.strip()
                birth_node = find_child_by_local_name(person_node, "birthTime")
                if birth_node is not None:
                    patient_birthdate = birth_node.get("value", "")
            break

    # Find RHYTHM series
    target_series = None
    all_series = [elem for elem in root.iter() if get_local_tag(elem.tag) == "series"]
    
    for series in all_series:
        code_node = find_child_by_local_name(series, "code")
        if code_node is not None:
            code_val = code_node.get("code", "").upper()
            if "RHYTHM" in code_val:
                target_series = series
                break
    
    if target_series is None and len(all_series) > 0:
        target_series = all_series[0]

    if target_series is None:
        raise ValueError("No Waveform Series found in HL7 file.")

    # Extract time
    exam_date = ""
    exam_time = ""
    
    eff_time = find_child_by_local_name(target_series, "effectiveTime")
    if eff_time is not None:
        low_node = find_child_by_local_name(eff_time, "low")
        if low_node is not None:
            val = low_node.get("value", "")
            if len(val) >= 8: exam_date = val[:8]
            if len(val) >= 14: exam_time = val[8:14]
    
    waveform_component = None
    
    for child in target_series:
        tag = get_local_tag(child.tag)
        if tag == "component":
            # Check if this component has a sequenceSet
            if find_child_by_local_name(child, "sequenceSet") is not None:
                waveform_component = child
                break
    
    if waveform_component is None:
         raise ValueError("No valid waveform component found in RHYTHM series.")

    # Extract waveforms
    signals_dict = {}
    sampling_freq = 500.0 # Default
    
    for seq in waveform_component.iter():
        if get_local_tag(seq.tag) != "sequence":
            continue

        code_node = find_child_by_local_name(seq, "code")
        if code_node is None: continue
        code_attr = code_node.get("code", "")
        
        # Time info
        if "TIME" in code_attr:
            val_node = find_child_by_local_name(seq, "value")
            if val_node is not None:
                inc_node = find_child_by_local_name(val_node, "increment")
                if inc_node is not None:
                    try:
                        val_str = inc_node.get("value", "1").replace(',', '.')
                        val = float(val_str)
                        unit = inc_node.get("unit", "s")
                        if val > 0:
                            if unit == "s": sampling_freq = 1.0 / val
                            elif unit == "ms": sampling_freq = 1000.0 / val
                            elif unit == "us": sampling_freq = 1000000.0 / val
                    except ValueError:
                        pass
        
        # Lead data
        elif "MDC_ECG_LEAD_" in code_attr:
            lead_name = code_attr.replace("MDC_ECG_LEAD_", "")
            val_node = find_child_by_local_name(seq, "value")
            if val_node is not None:
                digits_node = find_child_by_local_name(val_node, "digits")
                if digits_node is not None and digits_node.text:
                    clean_text = digits_node.text.replace('\n', ' ').strip()
                    try:
                        # np.fromstring was removed in NumPy 2.x
                        data = np.array([float(t) for t in clean_text.split()], dtype=float)

                        # Scale to uV
                        scale_node = find_child_by_local_name(val_node, "scale")
                        if scale_node is not None:
                            scale_val = scale_node.get("value", "1.0").replace(',', '.')
                            scale = float(scale_val)
                            unit = scale_node.get("unit", "uV")
                            if unit == "mV": scale *= 1000.0
                            data = data * scale
                        
                        signals_dict[lead_name] = data
                    except Exception:
                        pass

    if not signals_dict:
        raise ValueError("No waveform data found in the selected HL7 component.")

    # Map to FINAL_LEADS
    ordered_signals = []
    found_leads = []

    # Check for common keys
    for ref_lead in FINAL_LEADS:
        if ref_lead in signals_dict:
            ordered_signals.append(signals_dict[ref_lead])
            found_leads.append(ref_lead)
        else:
            for k in signals_dict.keys():
                if k.upper() == ref_lead.upper():
                    ordered_signals.append(signals_dict[k])
                    found_leads.append(ref_lead)
                    break
    
    if ordered_signals:
        collected, signal_names = ordered_signals, found_leads
    else:
        collected, signal_names = list(signals_dict.values()), list(signals_dict.keys())

    # Leads should all be the same length, but exports do get truncated. Pad
    # the short ones to the longest instead of failing the whole record.
    target_len = max(lead.size for lead in collected)
    signals = np.vstack([zero_pad(lead, target_len) for lead in collected])

    meta = {
        "source": "hl7",
        "fs": sampling_freq,
        "signal_names": signal_names,
        "n_samples": signals.shape[1],
        "patient_id": patient_id,
        "patient_firstname": patient_firstname,
        "patient_lastname": patient_lastname,
        "patient_birthdate": patient_birthdate,
        "patient_sex": gender,
        "patient_age": "", 
        "patient_weight": "",
        "patient_height": "",
        "exam_date": exam_date,
        "exam_time": exam_time,
        "pacemaker": "",
        "pon": None, 
        "poff": None, 
        "pdur": None
    }
    return signals, meta

# XML Reader
def read_xml(file_path):
    # Check if file is HL7 aECG
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        if 'AnnotatedECG' in root.tag:
            try:
                return read_hl7_aecg(file_path)
            except Exception as e:
                # No printing here: the caller logs through its own callback,
                # which is the only channel the GUI can show.
                raise ValueError(f"HL7 aECG parsing failed: {e}") from e
    except Exception as e:
        if "AnnotatedECG" in str(e) or "HL7" in str(e):
             raise e 
        pass

    # Standard XML Reader
    with open(file_path,'r',encoding='utf-8',errors='ignore') as f:
        xml_text = f.read()
    tree = parse_xml(file_path)
    root = tree.getroot()

    patdata = root.find('.//patdata')
    patient_id = ""
    patient_firstname = ""
    patient_lastname = ""
    patient_birthdate = ""
    gender = ""
    weight = ""
    height = ""
    pacemaker = ""
    if patdata is not None:
        patient_id = patdata.findtext('id',default='').strip()
        patient_firstname = patdata.findtext('firstname',default='').strip()
        patient_lastname = patdata.findtext('lastname',default='').strip()
        patient_birthdate = patdata.findtext('birthdate',default='').strip()
        gender = patdata.findtext('gender',default='').strip().upper()
        for val_node in patdata.findall('.//value'):
            try:
                unit_node = val_node.getnext()
                if unit_node is not None and unit_node.text:
                    unit = unit_node.text.lower()
                    if 'kg' in unit: weight = val_node.text.strip()
                    elif 'cm' in unit: height = val_node.text.strip()
            except:
                continue
        pacemaker = patdata.findtext('.//pacemaker/value',default='').strip().upper()

    exam_date = find_first_tag_text(root,'date')
    exam_time = find_first_tag_text(root,'time')

    rhythms_block = find_ecg_rhythms(root)
    if rhythms_block is None:
        snippet = extract_rhythms_snippet(xml_text)
        if snippet:
            if lxml_etree is None:
                raise ValueError("lxml is required to parse snippet but not available.")
            rhythms_block = lxml_etree.fromstring(snippet) # Use lxml for parsing snippet
        else:
            raise ValueError("Could not find ECG_RHYTHMS block in XML.")

    first_channel = rhythms_block.find('.//channel')
    if first_channel is None:
        first_channel = rhythms_block.find('.//ECG_RHYTHM')

    TARGET_SAMPLES=None
    if first_channel is not None:
        for tag in ('data','SAMPLES'):
            node = first_channel.find(tag)
            if node is not None and node.text:
                tokens=[t.strip() for t in re.split('[, ]+', node.text.strip()) if t.strip()]
                TARGET_SAMPLES=len(tokens); break
    if not TARGET_SAMPLES: TARGET_SAMPLES=5000

    sampling_freq = detect_sampling_frequency(root) or 500.0

    lead_data={}
    signal_names = []
    channels = rhythms_block.findall('.//channel') + rhythms_block.findall('.//ECG_RHYTHM')
    for chan in channels:
        name=None
        for tag in ('name','NAME'):
            node=chan.find(tag)
            if node is not None and node.text:
                name=node.text.strip().upper(); break
        if not name:
            attr = chan.get('lead') or chan.get('name')
            if isinstance(attr,str):
                name=attr.strip().upper()
        if not name: continue
        data_node=None
        for tag in ('data','SAMPLES'):
            node=chan.find(tag)
            if node is not None and node.text:
                data_node=node; break
        if data_node is None: continue
        samples=parse_samples(data_node.text)
        if samples.size != TARGET_SAMPLES:
            samples=resample(samples,TARGET_SAMPLES)
        if name not in lead_data:
            # A repeated lead name would leave signal_names longer than the
            # signal matrix, and every lead after it mapped to the wrong row.
            signal_names.append(name)
        lead_data[name]=samples

    if not lead_data:
        raise ValueError("No lead waveforms could be parsed from the ECG_RHYTHMS block.")
    signals = np.array([lead_data[name] for name in signal_names])

    meta = {
        "source": "xml",
        "fs": sampling_freq,
        "signal_names": signal_names,
        "n_samples": signals.shape[1],
        "patient_id": patient_id,
        "patient_firstname": patient_firstname,
        "patient_lastname": patient_lastname,
        "patient_birthdate": patient_birthdate,
        "patient_sex": gender,
        "patient_weight": weight,
        "patient_height": height,
        "exam_date": exam_date,
        "exam_time": exam_time,
        "pacemaker": pacemaker,
    }
    return signals, meta