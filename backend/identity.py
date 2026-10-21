"""Local profile identity with safe, fictional defaults for published source."""
import os
from pathlib import Path


def _load_local_settings():
    if os.environ.get('BYEFAN_DISABLE_LOCAL_CONFIG') == '1':
        return
    path = Path(__file__).resolve().parents[1] / '.env.local'
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return
    for line in lines:
        key, separator, value = line.partition('=')
        if separator and key.strip() in {'BYEFAN_TARGET_USERNAME', 'BYEFAN_OBSERVER_USERNAME'}:
            os.environ.setdefault(key.strip(), value.strip())


_load_local_settings()

TARGET = os.environ.get('BYEFAN_TARGET_USERNAME', 'example_target')
OBSERVER = os.environ.get('BYEFAN_OBSERVER_USERNAME', 'example_observer')
