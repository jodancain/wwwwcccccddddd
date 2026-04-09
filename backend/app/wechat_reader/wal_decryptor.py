"""Decrypt WAL (Write-Ahead Log) pages and apply them to decrypted MSG.db.

WeChat's WAL has a standard SQLite WAL header (unencrypted) followed by
frames containing encrypted 4096-byte pages (same AES-CBC as the main .db).
This allows us to read new messages from WAL without waiting for WeChat
to checkpoint.
"""
import hashlib
import struct
import sqlite3
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    from Crypto.Cipher import AES
except ImportError:
    from Cryptodome.Cipher import AES


def decrypt_wal_to_db(
    key_hex: str,
    encrypted_db_path: str,
    wal_path: str,
    decrypted_db_path: str,
) -> int:
    """Read encrypted WAL frames, decrypt pages, apply to decrypted DB.

    Returns number of pages applied.
    """
    wal = Path(wal_path)
    if not wal.exists() or wal.stat().st_size < 32:
        return 0

    # Derive the decryption key (same as wdecipher)
    encrypted_data = Path(encrypted_db_path).read_bytes()
    salt = encrypted_data[:16]
    password = bytes.fromhex(key_hex.strip())
    pk = hashlib.pbkdf2_hmac("sha1", password, salt, 64000, 32)

    # Read WAL file
    wal_data = wal.read_bytes()

    # Parse WAL header (32 bytes, unencrypted)
    # https://www.sqlite.org/walformat.html
    magic = struct.unpack(">I", wal_data[:4])[0]
    if magic not in (0x377F0682, 0x377F0683):
        logger.warning(f"Invalid WAL magic: 0x{magic:08x}")
        return 0

    # WAL header: magic(4) + format(4) + page_size(4) + checkpoint_seq(4) +
    #             salt1(4) + salt2(4) + checksum1(4) + checksum2(4)
    page_size = struct.unpack(">I", wal_data[8:12])[0]
    if page_size == 0:
        page_size = 4096

    # Frame header is 24 bytes, followed by page_size bytes of page data
    frame_header_size = 24
    frame_size = frame_header_size + page_size
    wal_header_size = 32

    num_frames = (len(wal_data) - wal_header_size) // frame_size
    if num_frames <= 0:
        return 0

    logger.info(f"WAL: {num_frames} frames, page_size={page_size}")

    # Decrypt each WAL frame's page data
    decrypted_pages = {}  # page_number -> decrypted_data
    applied = 0

    for i in range(num_frames):
        offset = wal_header_size + i * frame_size
        frame_header = wal_data[offset:offset + frame_header_size]
        page_data = wal_data[offset + frame_header_size:offset + frame_size]

        if len(page_data) < page_size:
            break

        # Frame header: page_number(4) + db_size_after(4) + salt1(4) + salt2(4) +
        #               checksum1(4) + checksum2(4)
        page_number = struct.unpack(">I", frame_header[:4])[0]

        # Decrypt the page data (same AES-CBC as main db, but each page has its own IV)
        # Page structure: [data(4048)] [IV(16)] [HMAC(16)] [padding(12)]
        # Total reserved = 48 bytes
        try:
            iv = page_data[-48:-32]
            encrypted_content = page_data[:-48]
            cipher = AES.new(pk, AES.MODE_CBC, iv)
            decrypted = cipher.decrypt(encrypted_content)
            # Store: decrypted_content + reserved_bytes
            decrypted_pages[page_number] = decrypted + page_data[-48:]
            applied += 1
        except Exception as e:
            # Skip bad frames
            continue

    if not decrypted_pages:
        return 0

    # Apply decrypted pages to the output DB file
    # Read existing decrypted DB
    db_path = Path(decrypted_db_path)
    if not db_path.exists():
        logger.warning("Decrypted DB not found, can't apply WAL pages")
        return 0

    db_data = bytearray(db_path.read_bytes())
    db_page_size = 4096  # Standard SQLite page size

    pages_written = 0
    for page_num, page_content in decrypted_pages.items():
        # Page numbers are 1-based
        offset = (page_num - 1) * db_page_size
        if offset + db_page_size <= len(db_data):
            # Overwrite existing page
            db_data[offset:offset + db_page_size] = page_content[:db_page_size]
            pages_written += 1
        elif offset == len(db_data):
            # Append new page
            db_data.extend(page_content[:db_page_size])
            pages_written += 1

    # Write back
    db_path.write_bytes(bytes(db_data))

    # Verify the DB is still valid
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("SELECT COUNT(*) FROM MSG")
        conn.close()
        logger.info(f"Applied {pages_written} WAL pages to {db_path.name}")
    except Exception as e:
        logger.error(f"DB corrupted after WAL apply: {e}")
        # The DB might be corrupted, but we'll let the next full decrypt fix it
        return -1

    return pages_written
