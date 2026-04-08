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
                raise RuntimeError("No WeChat process found. Make sure WeChat is running and logged in.")
            self.wx_info = infos[0]
            logger.info(f"WeChat info obtained: nickname={self.wx_info.get('nickname', 'N/A')}")
            return self.wx_info
        except ImportError:
            raise RuntimeError("wdecipher not installed. Run: pip install wdecipher")
        except Exception as e:
            raise RuntimeError(f"Failed to get WeChat info: {e}")

    def decrypt_databases(self) -> str:
        if not self.wx_info:
            self.initialize()

        try:
            import wdecipher

            db_key = self.wx_info.get("db_key") or self.wx_info.get("key")
            wx_dir = self.wx_info.get("wx_dir") or self.wx_info.get("filePath")

            if not db_key or not wx_dir:
                raise RuntimeError(f"Missing db_key or wx_dir in wx_info: {self.wx_info}")

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
            wdecipher.batch_decrypt_wx_db(db_key, target_dbs, output, merge_db=True)

            logger.info(f"Decrypted {len(target_dbs)} databases to {output}")
            return f"Decrypted {len(target_dbs)} databases"

        except ImportError:
            raise RuntimeError("wdecipher not installed")
        except Exception as e:
            raise RuntimeError(f"Decryption failed: {e}")

    def get_decrypted_db_path(self, db_type: str = "MSG") -> list[Path]:
        paths = []
        for f in self.output_dir.iterdir():
            if f.suffix == ".db" and db_type in f.stem:
                paths.append(f)
        return sorted(paths)
