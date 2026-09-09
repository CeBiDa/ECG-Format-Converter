import numpy as np

# Calculate missing leads using Einthoven's equations
def calculate_missing_leads(signals, signal_names):
    calculated = []
    lead_map = {name.upper(): i for i, name in enumerate(signal_names)}

    def get(lead):
        return signals[lead_map[lead.upper()]] if lead.upper() in lead_map else None

    if "I" not in lead_map and "II" in lead_map and "III" in lead_map:
        signals = np.vstack([signals, get("II") - get("III")])
        signal_names.append("I"); calculated.append("I"); lead_map["I"] = len(signal_names)-1
    if "II" not in lead_map and "I" in lead_map and "III" in lead_map:
        signals = np.vstack([signals, get("I") + get("III")])
        signal_names.append("II"); calculated.append("II"); lead_map["II"] = len(signal_names)-1
    if "III" not in lead_map and "II" in lead_map and "I" in lead_map:
        signals = np.vstack([signals, get("II") - get("I")])
        signal_names.append("III"); calculated.append("III"); lead_map["III"] = len(signal_names)-1
    if "AVR" not in lead_map and "I" in lead_map and "II" in lead_map:
        signals = np.vstack([signals, -0.5*(get("I") + get("II"))])
        signal_names.append("AVR"); calculated.append("AVR"); lead_map["AVR"] = len(signal_names)-1
    if "AVL" not in lead_map and "I" in lead_map and "III" in lead_map:
        signals = np.vstack([signals, 0.5*(get("I") - get("III"))])
        signal_names.append("AVL"); calculated.append("AVL"); lead_map["AVL"] = len(signal_names)-1
    if "AVF" not in lead_map and "II" in lead_map and "III" in lead_map:
        signals = np.vstack([signals, 0.5*(get("II") + get("III"))])
        signal_names.append("AVF"); calculated.append("AVF"); lead_map["AVF"] = len(signal_names)-1

    return signals, signal_names, calculated
