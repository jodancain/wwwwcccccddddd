import shutil
import time
from pathlib import Path
from loguru import logger

from app.config.settings import get_settings


class WeChatDecryptor:
    def __init__(self):
        self.settings = get_settings()
        self.output_dir = self.settings.decrypted_wx_dir
        self.wx_info = None

    def initialize(self) -> dict:
        try:
            import wdecipher
            infos = wdecipher.get_wx_infos()
            if not infos:
                raise RuntimeError("No WeChat process found.")
            self.wx_info = infos[0]
            logger.info(f"WeChat info: nickname={self.wx_info.get('nickname', 'N/A')}")
            return self.wx_info
        except ImportError:
            raise RuntimeError("wdecipher not installed")
        except Exception as e:
            raise RuntimeError(f"Failed to get WeChat info: {e}")

    def decrypt_databases(self) -> str:
        """Decrypt WeChat databases.

        Deletes old merged MSG.db before re-decrypting to ensure fresh data.
        """
        if not self.wx_info:
            self.initialize()

        try:
            import wdecipher

            db_key = self.wx_info.get("db_key") or self.wx_info.get("key")
            wx_dir = self.wx_info.get("wx_dir") or self.wx_info.get("filePath")

            if not db_key or not wx_dir:
                raise RuntimeError(f"Missing db_key or wx_dir")

            dbs = wdecipher.get_wx_dbs(wx_dir)
            if not dbs:
                raise RuntimeError(f"No databases found in {wx_dir}")

            target_dbs = [
                db for db in dbs
                if any(name in str(db) for name in ["MSG", "MicroMsg"])
            ]
            if not target_dbs:
                target_dbs = dbs

            output = str(self.output_dir)

            # Delete old merged MSG.db so it gets re-created with fresh data
            old_merged = self.output_dir / "MSG.db"
            if old_merged.exists():
                try:
                    old_merged.unlink()
                except Exception:
                    pass

            try:
                wdecipher.batch_decrypt_wx_db(db_key, target_dbs, output, merge_db=True)
                logger.info(f"Decrypted {len(target_dbs)} databases")
                return f"Decrypted {len(target_dbs)} databases"
            except Exception as e:
                logger.warning(f"Decrypt+merge failed: {e}")
                # Try without merge - parser can read individual files
                try:
                    wdecipher.batch_decrypt_wx_db(db_key, target_dbs, output, merge_db=False)
                    return f"Decrypted (no merge)"
                except Exception as e2:
                    logger.error(f"Decrypt failed: {e2}")
                    return "Using cached data"

        except ImportError:
            raise RuntimeError("wdecipher not installed")

    def get_decrypted_db_path(self, db_type: str = "MSG") -> list[Path]:
        paths = []
        for f in self.output_dir.iterdir():
            if f.suffix == ".db" and db_type in f.stem:
                paths.append(f)
        return sorted(paths)
