import sys
import importlib

def check_dependencies():
    """
    Checks for required packages and provides instructions if they are missing.
    """
    # List of (pip_package_name, import_name)
    packages = [
        ('numpy', 'numpy'),
        ('pandas', 'pandas'),
        ('tqdm', 'tqdm'),
        ('scipy', 'scipy'),
        ('pydicom', 'pydicom'),
        ('PyWavelets', 'pywt'),
        ('lxml', 'lxml'),
        ('EMD-signal', 'PyEMD'),
        ('wfdb', 'wfdb')
    ]

    print("Checking for required packages...")
    missing_packages = []
    for pip_name, import_name in packages:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing_packages.append(pip_name)

    if missing_packages:
        print("\nERROR: The following required packages are not installed:")
        for pkg in missing_packages:
            print(f"  - {pkg}")
        print("\nInstall them from the requirements file that ships with the repository:")
        print("    pip install -r requirements.txt")
        print("\nThe script will now exit.")
        sys.exit(1)
    else:
        print("All required packages are available.\n")
