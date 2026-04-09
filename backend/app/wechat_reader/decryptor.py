import shutil
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
        """Decrypt WeChat DBs to a fresh temp dir then swap into place.

        Always decrypts to a clean directory to avoid the 'already merged'
        skip issue. Then copies the result over the live file.

        Note: Messages in WeChat's WAL (Write-Ahead Log) won't appear until
        WeChat checkpoints them to the main .db file. This typically happens
        every few minutes automatically.
        """
        if not self.wx_info:
            self.initialize()

        try:
            import wdecipher

            db_key = self.wx_info.get("db_key") or self.wx_info.get("key")
            wx_dir = self.wx_info.get("wx_dir") or self.wx_info.get("filePath")

            if not db_key or not wx_dir:
                raise RuntimeError("Missing db_key or wx_dir")

            dbs = wdecipher.get_wx_dbs(wx_dir)
            if not dbs:
                raise RuntimeError(f"No databases found in {wx_dir}")

            # Only MSG + MicroMsg, skip MediaMSG for speed
            target_dbs = [
                d for d in dbs
                if d.get("type", "") in ("MSG", "MicroMsg")
                and "MediaMSG" not in d.get("path", "")
            ]
            if not target_dbs:
                target_dbs = [d for d in dbs if "MediaMSG" not in d.get("path", "")]

            # Decrypt to fresh temp dir (avoids merge-skip and file-lock issues)
            dec_tmp = self.output_dir / "_dec_tmp"
            if dec_tmp.exists():
                shutil.rmtree(str(dec_tmp), ignore_errors=True)
            dec_tmp.mkdir(parents=True, exist_ok=True)

            try:
                wdecipher.batch_decrypt_wx_db(db_key, target_dbs, str(dec_tmp), merge_db=True)
            except Exception as e:
                logger.warning(f"Decrypt failed: {e}")
                shutil.rmtree(str(dec_tmp), ignore_errors=True)
                return "Decrypt failed"

            # Swap fresh files into place
            for name in ["MSG.db", "MicroMsg.db"]:
                new_file = dec_tmp / name
                if new_file.exists():
                    try:
                        shutil.copy2(str(new_file), str(self.output_dir / name))
                    except Exception as e:
                        logger.warning(f"Swap {name} failed: {e}")

            shutil.rmtree(str(dec_tmp), ignore_errors=True)
            logger.info(f"Decrypted {len(target_dbs)} databases")
            return f"Decrypted {len(target_dbs)} databases"

        except ImportError:
            raise RuntimeError("wdecipher not installed")

    def get_decrypted_db_path(self, db_type: str = "MSG") -> list[Path]:
        paths = []
        for f in self.output_dir.iterdir():
            if f.suffix == ".db" and db_type in f.stem:
                paths.append(f)
        return sorted(paths)
