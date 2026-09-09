import os
import re
import numpy as np

from ecgproc.xml_utils import parse_xml, extract_rhythms_snippet, find_ecg_rhythms, find_first_tag_text, parse_samples, detect_sampling_frequency
from ecgproc.writers import write_dcm
from ecgproc.constants import FINAL_LEADS
from ecgproc import stats

def create_dicom_file(xml_file_path, output_folder, anonymize=False, override=False):
    os.makedirs(output_folder, exist_ok=True)
    try:
        base_name = os.path.splitext(os.path.basename(xml_file_path))[0]
        output_file_path = os.path.join(output_folder, base_name+".dcm")
        if os.path.exists(output_file_path) and not override:
            stats.count_skipped += 1
            return None, None

        with open(xml_file_path,'r',encoding='utf-8',errors='ignore') as f:
            xml_text = f.read()
        tree = parse_xml(xml_file_path)
        root = tree.getroot()

        # Extract values
        patdata = root.find('.//patdata')
        
        patient_id = ""
        patient_firstname = ""
        patient_lastname = ""
        patient_birthdate = ""
        gender = ""
        ethnic = ""
        weight, height, pacemaker = '', '', ''

        if patdata is not None:
            patient_id = patdata.findtext('id', default='').strip()
            patient_firstname = patdata.findtext('firstname', default='').strip()
            patient_lastname = patdata.findtext('lastname', default='').strip()
            patient_birthdate = patdata.findtext('birthdate', default='').strip()
            gender = patdata.findtext('gender',default='').strip().upper()
            ethnic = patdata.findtext('ethnic',default='').strip()
            for val_node in patdata.findall('.//value'):
                try:
                    unit_node = val_node.getnext()
                except AttributeError:
                    # ElementTree fallback parser: no getnext() sibling walk.
                    unit_node = None
                if unit_node is not None and unit_node.text:
                    unit = unit_node.text.lower()
                    if 'kg' in unit: weight = val_node.text.strip()
                    elif 'cm' in unit: height = val_node.text.strip()
            pacemaker = patdata.findtext('.//pacemaker/value',default='').strip().upper()

        if anonymize:
            patient_id = ""
            patient_firstname = ""
            patient_lastname = ""
            patient_birthdate = ""

        exam_date = find_first_tag_text(root,'date')
        exam_time = find_first_tag_text(root,'time')

        rhythms_block = find_ecg_rhythms(root)
        if rhythms_block is None:
            snippet = extract_rhythms_snippet(xml_text)
            if snippet:
                from ecgproc.xml_utils import lxml_etree
                if lxml_etree is None:
                    stats.count_failed += 1
                    return None, None
                rhythms_block = lxml_etree.fromstring(snippet)
            else:
                stats.count_failed += 1
                return None,None

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

        # leads
        lead_data={}
        channels = rhythms_block.findall('.//channel') + rhythms_block.findall('.//ECG_RHYTHM')
        for chan in channels:
            name=None
            for tag in ('name','NAME'):
                node=chan.find(tag)
                if node is not None and node.text: 
                    name=node.text.strip().upper(); break
            if not name:
                attr = chan.get('lead')
                if attr is None:
                    attr = chan.get('name')
                if isinstance(attr,str): 
                    name=attr.strip().upper()
            if not name: continue
            data_node=None
            for tag in ('data','SAMPLES'):
                node=chan.find(tag)
                if node is not None and node.text: 
                    data_node=node; break
            if data_node is None: continue
            from scipy.signal import resample
            samples=parse_samples(data_node.text)
            if samples.size!=TARGET_SAMPLES:
                samples=resample(samples,TARGET_SAMPLES)
            lead_data[name]=samples

        # Record what the file actually carried before the gaps are filled, so
        # the summary does not present a zero-filled lead as a measured one.
        original_leads = [ld for ld in FINAL_LEADS if ld in lead_data]
        missing_leads = [ld for ld in FINAL_LEADS if ld not in lead_data]
        for lead in missing_leads:
            lead_data[lead]=np.zeros(TARGET_SAMPLES)

        stacked_waveforms = np.stack([lead_data[ld] for ld in FINAL_LEADS], axis=1)

        # meta
        meta = {
            "source": "xml→dcm",
            "sampling_freq": sampling_freq,
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
            "n_samples": TARGET_SAMPLES,
            "signal_names": FINAL_LEADS,
            # The fast path derives nothing: a lead is either in the file or
            # zero-filled, so calculated_leads stays empty by construction.
            "original_leads": original_leads,
            "calculated_leads": [],
            "missing_leads": missing_leads,
        }

        # build DICOM
        write_dcm(output_file_path, stacked_waveforms, meta)

        stats.count_success+=1
        return stacked_waveforms, meta
    except Exception:
        stats.count_failed+=1
        return None,None
