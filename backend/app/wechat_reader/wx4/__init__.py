"""Weixin 4.x decryption primitives.

Vendored / adapted from https://github.com/ylytdeng/wechat-decrypt
License: author's original (Python port, WTFPL-like in the upstream search
result). Only the minimum subset needed for in-process key extraction and
database decryption is kept.
"""
from .crypto import (
    decrypt_database,
    decrypt_db_to_memory,
    decrypt_db_to_tempfile,
    verify_enc_key,
)
from .key_scanner import find_all_keys, auto_detect_db_dir

__all__ = [
    "decrypt_database",
    "decrypt_db_to_memory",
    "decrypt_db_to_tempfile",
    "verify_enc_key",
    "find_all_keys",
    "auto_detect_db_dir",
]
