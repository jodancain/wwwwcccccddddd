"""Windows key scanner for Weixin 4.x.

Scans readable memory of Weixin.exe processes for WCDB's hex-encoded
raw enc_key + salt cache pattern (x'<64hex><32hex>'). Verifies candidates
by HMAC-checking page 1 of each target DB.

Adapted from ylytdeng/wechat-decrypt's find_all_keys_windows + key_scan_common.
"""
import ctypes
import ctypes.wintypes as wt
import glob
import os
import re
import subprocess
from typing import Optional

from loguru import logger

from .crypto import PAGE_SZ, SALT_SZ, verify_enc_key

kernel32 = ctypes.windll.kernel32
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64),
        ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD),
        ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("_pad2", wt.DWORD),
    ]


def _get_weixin_pids() -> list[tuple[int, int]]:
    """Return [(pid, mem_kb), ...] sorted by memory desc."""
    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    )
    pids = []
    for line in r.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.strip('"').split('","')
        if len(parts) >= 5:
            try:
                pid = int(parts[1])
                mem = int(parts[4].replace(",", "").replace(" K", "").strip() or "0")
                pids.append((pid, mem))
            except ValueError:
                continue
    pids.sort(key=lambda x: x[1], reverse=True)
    return pids


def _read_mem(h, addr: int, sz: int) -> Optional[bytes]:
    buf = ctypes.create_string_buffer(sz)
    n = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(h, ctypes.c_uint64(addr), buf, sz, ctypes.byref(n))
    return buf.raw[: n.value] if ok else None


def _enum_regions(h) -> list[tuple[int, int]]:
    regs = []
    addr = 0
    mbi = MBI()
    while addr < 0x7FFFFFFFFFFF:
        if kernel32.VirtualQueryEx(h, ctypes.c_uint64(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        is_readable = (
            mbi.Protect
            and not (mbi.Protect & PAGE_NOACCESS)
            and not (mbi.Protect & PAGE_GUARD)
        )
        if mbi.State == MEM_COMMIT and is_readable and 0 < mbi.RegionSize < 500 * 1024 * 1024:
            regs.append((mbi.BaseAddress, mbi.RegionSize))
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt
    return regs


def _collect_db_files(db_dir: str):
    """Walk db_dir, return (db_files, salt_to_dbs).

    db_files: [(rel, abs, size, salt_hex, page1), ...]
    salt_to_dbs: {salt_hex: [rel, ...]}
    """
    db_files = []
    salt_to_dbs: dict[str, list[str]] = {}
    for root, _dirs, files in os.walk(db_dir):
        for name in files:
            if not name.endswith(".db") or name.endswith("-wal") or name.endswith("-shm"):
                continue
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
                if size < PAGE_SZ:
                    continue
                with open(path, "rb") as f:
                    page1 = f.read(PAGE_SZ)
            except OSError:
                continue
            if len(page1) < PAGE_SZ:
                continue
            rel = os.path.relpath(path, db_dir)
            salt_hex = page1[:SALT_SZ].hex()
            db_files.append((rel, path, size, salt_hex, page1))
            salt_to_dbs.setdefault(salt_hex, []).append(rel)
    return db_files, salt_to_dbs


_HEX_RE = re.compile(rb"x'([0-9a-fA-F]{64,192})'")


def _scan_region(data: bytes, db_files, salt_to_dbs, key_map, remaining_salts) -> int:
    """Scan a memory block, populate key_map as matches are verified."""
    matches = 0
    for m in _HEX_RE.finditer(data):
        hex_str = m.group(1).decode()
        matches += 1
        hex_len = len(hex_str)

        # enc_key (64 hex = 32 bytes) followed by salt (32 hex = 16 bytes)
        if hex_len == 96:
            enc_key_hex = hex_str[:64]
            salt_hex = hex_str[64:]
            if salt_hex in remaining_salts:
                enc_key = bytes.fromhex(enc_key_hex)
                for rel, _path, _sz, s, page1 in db_files:
                    if s == salt_hex and verify_enc_key(enc_key, page1):
                        key_map[salt_hex] = enc_key_hex
                        remaining_salts.discard(salt_hex)
                        break
        elif hex_len == 64:
            if not remaining_salts:
                continue
            enc_key = bytes.fromhex(hex_str)
            for rel, _path, _sz, salt_hex_db, page1 in db_files:
                if salt_hex_db in remaining_salts and verify_enc_key(enc_key, page1):
                    key_map[salt_hex_db] = hex_str
                    remaining_salts.discard(salt_hex_db)
                    break
        elif hex_len > 96 and hex_len % 2 == 0:
            enc_key_hex = hex_str[:64]
            salt_hex = hex_str[-32:]
            if salt_hex in remaining_salts:
                enc_key = bytes.fromhex(enc_key_hex)
                for rel, _path, _sz, s, page1 in db_files:
                    if s == salt_hex and verify_enc_key(enc_key, page1):
                        key_map[salt_hex] = enc_key_hex
                        remaining_salts.discard(salt_hex)
                        break
    return matches


def _cross_verify(db_files, salt_to_dbs, key_map):
    """Use already-found keys to match any remaining salts (same login →
    the same raw enc_key may decrypt several DBs with distinct salts)."""
    missing = set(salt_to_dbs.keys()) - set(key_map.keys())
    if not missing or not key_map:
        return
    for salt_hex in list(missing):
        for _rel, _path, _sz, s, page1 in db_files:
            if s != salt_hex:
                continue
            for _known_salt, known_key_hex in key_map.items():
                if verify_enc_key(bytes.fromhex(known_key_hex), page1):
                    key_map[salt_hex] = known_key_hex
                    break
            break


def find_all_keys(db_dir: str) -> dict[str, dict]:
    """Extract per-DB enc_keys by scanning Weixin.exe memory.

    Returns: {rel_path: {"enc_key": hex, "salt": hex, "size_mb": float}}
    Raises RuntimeError if Weixin.exe is not running or no keys found.
    """
    if not os.path.isdir(db_dir):
        raise RuntimeError(f"db_dir does not exist: {db_dir}")

    db_files, salt_to_dbs = _collect_db_files(db_dir)
    if not db_files:
        raise RuntimeError(f"No .db files found under {db_dir}")
    logger.info(f"Prepared {len(db_files)} encrypted DB samples for key verification")

    pids = _get_weixin_pids()
    if not pids:
        raise RuntimeError("Weixin.exe is not running")

    key_map: dict[str, str] = {}
    remaining_salts = set(salt_to_dbs.keys())

    opened = 0
    regions_read = 0
    candidate_count = 0
    for pid, mem_kb in pids:
        if not remaining_salts:
            break
        h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not h:
            logger.warning(f"Cannot open Weixin.exe pid={pid} for memory read")
            continue
        opened += 1
        logger.info(f"Scanning Weixin.exe pid={pid} ({mem_kb} KB)")
        try:
            for base, size in _enum_regions(h):
                if not remaining_salts:
                    break
                data = _read_mem(h, base, size)
                if not data:
                    continue
                regions_read += 1
                candidate_count += _scan_region(data, db_files, salt_to_dbs, key_map, remaining_salts)
        finally:
            kernel32.CloseHandle(h)

    _cross_verify(db_files, salt_to_dbs, key_map)
    logger.info(
        "Key scan summary: "
        f"processes_opened={opened}, regions_read={regions_read}, "
        f"candidates={candidate_count}, salts_matched={len(key_map)}/{len(salt_to_dbs)}"
    )

    result: dict[str, dict] = {}
    for rel, _path, sz, salt_hex, _page1 in db_files:
        if salt_hex in key_map:
            result[rel] = {
                "enc_key": key_map[salt_hex],
                "salt": salt_hex,
                "size_mb": round(sz / 1024 / 1024, 2),
            }
    if not result:
        raise RuntimeError("Scanned Weixin.exe memory but no keys matched any DB")
    return result


def auto_detect_db_dir() -> Optional[str]:
    """Find %APPDATA%\\Tencent\\xwechat\\config\\*.ini → data root → db_storage.

    Fallback: search %USERPROFILE%\\Documents\\xwechat_files,
    %USERPROFILE%\\OneDrive\\xwechat_files, and drive-root xwechat_files dirs.
    """
    appdata = os.environ.get("APPDATA", "")
    cfg_dir = os.path.join(appdata, "Tencent", "xwechat", "config")
    data_roots: list[str] = []
    if os.path.isdir(cfg_dir):
        for ini_file in glob.glob(os.path.join(cfg_dir, "*.ini")):
            content = None
            for enc in ("utf-8", "gbk"):
                try:
                    with open(ini_file, "r", encoding=enc) as f:
                        content = f.read(1024).strip()
                    break
                except (UnicodeDecodeError, OSError):
                    continue
            if not content or any(c in content for c in "\n\r\x00"):
                continue
            if os.path.isdir(content):
                data_roots.append(content)

    # Fallback locations users commonly see
    profile = os.environ.get("USERPROFILE", "")
    if profile:
        for rel in ("Documents/xwechat_files", "OneDrive/xwechat_files", "Documents/WeChat Files/xwechat_files"):
            p = os.path.join(profile, rel.replace("/", os.sep))
            if os.path.isdir(p):
                data_roots.append(os.path.dirname(p))

    candidates = []
    seen = set()
    for root in data_roots:
        pattern = os.path.join(root, "xwechat_files", "*", "db_storage")
        for match in glob.glob(pattern):
            norm = os.path.normcase(os.path.normpath(match))
            if os.path.isdir(match) and norm not in seen:
                seen.add(norm)
                candidates.append(match)
        # Some installs put db_storage directly at root/<wxid>/db_storage
        pattern2 = os.path.join(root, "*", "db_storage")
        for match in glob.glob(pattern2):
            norm = os.path.normcase(os.path.normpath(match))
            if os.path.isdir(match) and norm not in seen:
                seen.add(norm)
                candidates.append(match)

    if not candidates:
        return None

    # Prefer the most recently updated (by mtime of message/ subdir)
    def _recency(p: str) -> float:
        msg_dir = os.path.join(p, "message")
        target = msg_dir if os.path.isdir(msg_dir) else p
        try:
            return os.path.getmtime(target)
        except OSError:
            return 0.0

    candidates.sort(key=_recency, reverse=True)
    return candidates[0]
