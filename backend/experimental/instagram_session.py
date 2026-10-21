"""Windows user-bound DPAPI session storage. Never stores a password."""
import ctypes
import json
import os
from pathlib import Path
from ctypes import wintypes

ROOT = Path(__file__).resolve().parents[2]


def session_path():
    if os.name != "nt":
        raise RuntimeError("windows_dpapi_required")
    path = (Path(os.environ["LOCALAPPDATA"]) / "ByeFan" / "instagram-session.dpapi").resolve()
    if path.is_relative_to(ROOT):
        raise RuntimeError("session_must_be_outside_repository")
    return path


def crypt(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("windows_dpapi_required")
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = library.CryptUnprotectData if decrypt else library.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError("session_decryption_failed" if decrypt else "session_encryption_failed")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def save_session(settings, username):
    # Explicitly remove credential fields even if a future library adds them.
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items()
                    if k.lower() not in {"password", "verification_code", "two_factor_code"}}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    payload = json.dumps({"secondary_username": username, "settings": strip(settings)}).encode()
    encrypted = crypt(payload)
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.dpapi")
    temporary.write_bytes(encrypted)
    temporary.replace(path)


def load_session():
    return json.loads(crypt(session_path().read_bytes(), decrypt=True))
