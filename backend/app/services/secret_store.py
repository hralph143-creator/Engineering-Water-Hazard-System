"""Windows DPAPI helpers for local, user-scoped secrets."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys


class SecretStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise SecretStoreError("本机密钥加密仅支持 Windows")
    source = ctypes.create_string_buffer(data)
    source_blob = _DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if decrypt:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source_blob), None, None, None, None, flags, ctypes.byref(output_blob),
        )
    else:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source_blob), "ZhiyuShield AI Key", None, None, None,
            flags, ctypes.byref(output_blob),
        )
    if not ok:
        raise SecretStoreError(f"Windows DPAPI 操作失败（错误码 {ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def protect_secret(value: str) -> bytes:
    return _crypt(value.encode("utf-8"), decrypt=False)


def unprotect_secret(value: bytes) -> str:
    try:
        return _crypt(value, decrypt=True).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretStoreError("本机密钥数据无效") from exc
