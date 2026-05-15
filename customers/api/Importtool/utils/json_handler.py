# cdr_handle/utils/json_handler.py
import json
import os

from ..utils.get_path import get_path


class CDRConfigLoader:
    """Loads CDR config, headers, and date formats from JSON files."""

    def __init__(self):
        self._cache = {}

    def load_cdr_headers(self, path):
        path = get_path(path)
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def load_mandatory_headers(self, path):
        path = get_path(path)
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def load_mcc_mnc(self, path):
        path = get_path(path)
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def load_call_types(self, path):
        path = get_path(path)
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def load_cache_date_formats(self, path):
        path = get_path(path)
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def load_full_date_formats(self, path):
        path = get_path(path)
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def load_shuffle_columns(self,path):
        path = get_path(path)
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)