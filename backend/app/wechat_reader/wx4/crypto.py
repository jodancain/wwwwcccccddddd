"""SQLCipher 4 page decryption for Weixin 4.x databases.

Parameters (fixed by WCDB / SQLCipher 4):
  - AES-256-CBC
  - HMAC-SHA512
  - page_size = 4096, reserve = 80 (IV 16 + HMAC 64)
  - KDF: PBKDF2-HMAC-SHA512, 256000 iterations. Some scanners return the
    32-byte database passphrase, while older cache-pattern scanners may return
    the already-derived raw enc_key. This module accepts both.
"""
import hashlib
import hmac as hmac_mod
import os
import struct
import tempfile

from Crypto.Cipher import AES

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80  # IV(16) + HMAC(64)
SQLITE_HDR = b"SQLite format 3\x00"
KDF_ITER = 256000


def derive_mac_key(enc_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def derive_enc_key(passphrase: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", passphrase, salt, KDF_ITER, dklen=KEY_SZ)


def key_material_to_enc_key(key_material: bytes, db_page1: bytes) -> bytes:
    """Return the AES enc_key for either V4 passphrase or raw enc_key input."""
    salt = db_page1[:SALT_SZ]
    derived = derive_enc_key(key_material, salt)
    if verify_raw_enc_key(derived, db_page1):
        return derived
    if verify_raw_enc_key(key_material, db_page1):
        return key_material
    return b""


def verify_raw_enc_key(enc_key: bytes, db_page1: bytes) -> bool:
    if len(enc_key) != KEY_SZ:
        return False
    salt = db_page1[:SALT_SZ]
    mac_key = derive_mac_key(enc_key, salt)
    hmac_data = db_page1[SALT_SZ: PAGE_SZ - RESERVE_SZ + IV_SZ]
    stored_hmac = db_page1[PAGE_SZ - HMAC_SZ: PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac


def verify_enc_key(key_material: bytes, db_page1: bytes) -> bool:
    return bool(key_material_to_enc_key(key_material, db_page1))


def _decrypt_page(enc_key: bytes, page_data: bytes, pgno: int) -> bytes:
    iv = page_data[PAGE_SZ - RESERVE_SZ: PAGE_SZ - RESERVE_SZ + IV_SZ]
    if pgno == 1:
        encrypted = page_data[SALT_SZ: PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return bytes(SQLITE_HDR + decrypted + b"\x00" * RESERVE_SZ)
    encrypted = page_data[: PAGE_SZ - RESERVE_SZ]
    cipher = AES.new(enc_key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted)
    return decrypted + b"\x00" * RESERVE_SZ


def decrypt_db_to_memory(db_path: str, enc_key: bytes) -> bytes:
    """Decrypt entire DB into an in-memory bytes buffer."""
    file_size = os.path.getsize(db_path)
    total_pages = file_size // PAGE_SZ
    if file_size % PAGE_SZ != 0:
        total_pages += 1

    chunks = []
    with open(db_path, "rb") as fin:
        for pgno in range(1, total_pages + 1):
            page = fin.read(PAGE_SZ)
            if len(page) < PAGE_SZ:
                if not page:
                    break
                page = page + b"\x00" * (PAGE_SZ - len(page))
            chunks.append(_decrypt_page(enc_key, page, pgno))
    return b"".join(chunks)


def decrypt_database(db_path: str, out_path: str, enc_key: bytes) -> bool:
    """Decrypt DB file and write to out_path. Accepts V4 passphrase or raw key."""
    file_size = os.path.getsize(db_path)
    if file_size < PAGE_SZ:
        return False
    total_pages = file_size // PAGE_SZ
    if file_size % PAGE_SZ != 0:
        total_pages += 1

    with open(db_path, "rb") as fin:
        page1 = fin.read(PAGE_SZ)
    if len(page1) < PAGE_SZ:
        return False
    enc_key = key_material_to_enc_key(enc_key, page1)
    if not enc_key:
        return False

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(db_path, "rb") as fin, open(out_path, "wb") as fout:
        for pgno in range(1, total_pages + 1):
            page = fin.read(PAGE_SZ)
            if len(page) < PAGE_SZ:
                if not page:
                    break
                page = page + b"\x00" * (PAGE_SZ - len(page))
            fout.write(_decrypt_page(enc_key, page, pgno))
    return True


def decrypt_db_to_tempfile(db_path: str, enc_key: bytes, suffix: str = ".tmp") -> str:
    """Decrypt to a temp file and return its path. Caller must delete it."""
    data = decrypt_db_to_memory(db_path, enc_key)
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="wx4_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        os.unlink(tmp_path)
        raise
    return tmp_path
