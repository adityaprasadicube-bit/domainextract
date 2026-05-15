import os
import sys

def get_path(path):
    if os.path.isabs(path) and os.path.exists(path):
        return path  # Absolute path works
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    full_path = os.path.join(base_path, path)
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"File not found: {full_path}")
    return full_path
