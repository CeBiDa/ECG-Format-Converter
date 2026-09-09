# ECG Processing Constants
FINAL_LEADS = ['I','II','III','AVR','AVL','AVF','V1','V2','V3','V4','V5','V6']

# Input formats the readers understand (.dat/.hea pairs count as one WFDB record)
SUPPORTED_EXTENSIONS = [".mat", ".dat", ".hea", ".xml", ".hl7", ".csv", ".asc", ".dcm"]

# Formats the writers can produce via --format
OUTPUT_FORMATS = ["csv", "xml", "dcm", "hl7", "wfdb", "mat", "asc"]
