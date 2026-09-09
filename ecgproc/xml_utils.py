import re
import numpy as np

try:
    from lxml import etree as lxml_etree
    _xml_parser = lxml_etree.XMLParser(recover=True)
    def parse_xml(path): 
        return lxml_etree.parse(path, _xml_parser)
except ImportError:
    lxml_etree = None
    _xml_parser = None
    import xml.etree.ElementTree as std_etree
    def parse_xml(path): 
        return std_etree.parse(path)

def find_ecg_rhythms(root):
    for wd in root.findall('.//wavedata'):
        tnode = wd.find('type')
        if tnode is not None and tnode.text and tnode.text.strip().upper()=="ECG_RHYTHMS":
            return wd
        atype = wd.get('type')
        if atype and atype.strip().upper()=="ECG_RHYTHMS":
            return wd
    return None

def extract_rhythms_snippet(xml_text):
    pattern = r'(<wavedata[\s\S]*?<type>\s*ECG_RHYTHMS\s*</type>[\s\S]*?</wavedata>)'
    match = re.search(pattern, xml_text, flags=re.IGNORECASE)
    return match.group(1) if match else None

def parse_samples(raw_text):
    sep = ',' if ',' in raw_text else ' '
    tokens = [t.strip() for t in raw_text.split(sep) if t.strip()]
    return np.array([float(t) for t in tokens], dtype=float)

def find_first_tag_text(root, tagname):
    node = root.find(f'.//{tagname}')
    return node.text.strip() if node is not None and node.text else ''

def detect_sampling_frequency(root):
    sr_node = root.find('.//resolution/samplerate/value')
    if sr_node is not None and sr_node.text:
        val = sr_node.text.strip().replace(',','.')
        if val.replace('.','').isdigit():
            return float(val)
    for tag in ['samplerate','samplingrate','samplespersecond']:
        val = find_first_tag_text(root, tag)
        if val and val.replace('.','').isdigit():
            return float(val)
    chan = root.find('.//channel')
    if chan is None:
        chan = root.find('.//ECG_RHYTHM')
    if chan is not None:
        for attr in ['samplerate','samplingrate','samplespersecond']:
            val = chan.get(attr)
            if val and val.replace('.','').isdigit():
                return float(val)
    return None