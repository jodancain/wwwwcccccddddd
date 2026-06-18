"""Personal-model registry.

The registry is a small JSON file (``data/models/registry.json``) that
describes every imported model directory the user wants to be able to load
into the inference server. Two kinds of entries are supported:

  * **full**  — a standalone HuggingFace model directory (has ``config.json``
    plus one or more ``model*.safetensors`` / ``pytorch_model*.bin`` files).
    Loads with a single ``from_pretrained`` call.

  * **lora**  — a PEFT LoRA adapter directory (has ``adapter_config.json``
    plus ``adapter_model.safetensors``). Requires a base model; the registry
    reads ``base_model_name_or_path`` from the adapter config as a default,
    which the user can override via the import API.

The registry is the single source of truth for which model the inference
server loads. The active model id is stored alongside the entries so that
the selection survives backend restarts.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional

from app.config.settings import get_settings

MODEL_TYPE_FULL = "full"
MODEL_TYPE_LORA = "lora"

_FULL_WEIGHT_GLOBS = ("*.safetensors", "pytorch_model*.bin", "model*.safetensors")
_FULL_MARKERS = ("config.json",)
_LORA_MARKERS = ("adapter_config.json",)


def _has_any(dir_path: Path, patterns: Iterable[str]) -> bool:
    return any(next(dir_path.glob(p), None) is not None for p in patterns)


def detect_model_type(path: Path) -> Optional[str]:
    """Return ``"full"``, ``"lora"`` or ``None`` for the given directory."""
    if not path.is_dir():
        return None
    has_lora_marker = all((path / m).exists() for m in _LORA_MARKERS)
    if has_lora_marker:
        return MODEL_TYPE_LORA
    has_full_marker = all((path / m).exists() for m in _FULL_MARKERS)
    if has_full_marker and _has_any(path, _FULL_WEIGHT_GLOBS):
        return MODEL_TYPE_FULL
    return None


def read_lora_base_path(lora_dir: Path) -> str:
    """Read ``base_model_name_or_path`` from a PEFT adapter config."""
    cfg_path = lora_dir / "adapter_config.json"
    if not cfg_path.exists():
        return ""
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return cfg.get("base_model_name_or_path", "") or ""


class ModelRegistry:
    """Thread-safe JSON-backed model registry."""

    def __init__(self, registry_file: Optional[Path] = None) -> None:
        settings = get_settings()
        data_dir = Path(settings.DATA_DIR)
        self._file = registry_file or (data_dir / "models" / "registry.json")
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self._file.exists():
            self._write({"version": 1, "active_id": "", "models": []})
        # Auto-register the default LoRA training output on first launch so a
        # user who has already run the full pipeline sees their model without
        # having to click "import".
        self._autoseed_trained_lora()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _read(self) -> dict:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "active_id": "", "models": []}

    def _write(self, data: dict) -> None:
        tmp = self._file.with_suffix(self._file.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(self._file))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def list_models(self) -> list[dict]:
        with self._lock:
            data = self._read()
        out: list[dict] = []
        for m in data.get("models", []):
            entry = dict(m)
            entry["path_exists"] = bool(entry.get("path")) and Path(entry["path"]).exists()
            if entry.get("type") == MODEL_TYPE_LORA:
                entry["base_exists"] = bool(entry.get("base_path")) and Path(entry["base_path"]).exists()
            else:
                entry["base_exists"] = True
            entry["is_active"] = entry["id"] == data.get("active_id", "")
            out.append(entry)
        # Active model first, then newest on top
        out.sort(key=lambda m: (not m["is_active"], -int(m.get("created_at", 0) or 0)))
        return out

    def get_active(self) -> Optional[dict]:
        with self._lock:
            data = self._read()
        active_id = data.get("active_id", "")
        if not active_id:
            return None
        for m in data.get("models", []):
            if m["id"] == active_id:
                return m
        return None

    def add_model(
        self,
        path: str,
        name: str = "",
        base_path: str = "",
        model_type: str = "",
        notes: str = "",
    ) -> dict:
        """Register a model directory. Raises ValueError on invalid input."""
        if not path:
            raise ValueError("path is required")
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            raise ValueError(f"not a directory: {p}")

        detected = detect_model_type(p)
        if model_type and model_type not in (MODEL_TYPE_FULL, MODEL_TYPE_LORA):
            raise ValueError(f"unknown type: {model_type}")
        final_type = model_type or detected
        if not final_type:
            raise ValueError(
                f"{p} is not a recognisable model directory (need either "
                f"config.json + *.safetensors for a full model, or "
                f"adapter_config.json for a LoRA)"
            )

        final_base = base_path
        if final_type == MODEL_TYPE_LORA:
            if not final_base:
                final_base = read_lora_base_path(p)
            if not final_base:
                raise ValueError(
                    "LoRA adapter requires a base model path. Provide base_path "
                    "or ensure adapter_config.json has base_model_name_or_path."
                )
            base_p = Path(final_base).expanduser()
            if not base_p.is_dir():
                raise ValueError(f"base_path does not exist: {base_p}")
            final_base = str(base_p.resolve())

        with self._lock:
            data = self._read()
            # De-dupe: same absolute path → update existing instead of adding
            for existing in data["models"]:
                if Path(existing.get("path", "")).resolve() == p:
                    existing["name"] = name or existing.get("name") or p.name
                    existing["type"] = final_type
                    if final_type == MODEL_TYPE_LORA:
                        existing["base_path"] = final_base
                    if notes:
                        existing["notes"] = notes
                    existing["updated_at"] = int(time.time())
                    self._write(data)
                    return dict(existing)

            entry = {
                "id": uuid.uuid4().hex[:12],
                "name": name or p.name,
                "type": final_type,
                "path": str(p),
                "base_path": final_base if final_type == MODEL_TYPE_LORA else "",
                "notes": notes,
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            }
            data["models"].append(entry)
            self._write(data)
        return entry

    def remove(self, model_id: str) -> bool:
        with self._lock:
            data = self._read()
            before = len(data["models"])
            data["models"] = [m for m in data["models"] if m["id"] != model_id]
            if data.get("active_id") == model_id:
                data["active_id"] = ""
            changed = len(data["models"]) != before
            if changed:
                self._write(data)
        return changed

    def set_active(self, model_id: str) -> Optional[dict]:
        with self._lock:
            data = self._read()
            for m in data["models"]:
                if m["id"] == model_id:
                    data["active_id"] = model_id
                    self._write(data)
                    return dict(m)
        return None

    def clear_active(self) -> None:
        with self._lock:
            data = self._read()
            data["active_id"] = ""
            self._write(data)

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    def default_scan_roots(self) -> list[Path]:
        settings = get_settings()
        roots: list[Path] = []
        extra = Path("D:/WeChatAI_models")
        if extra.is_dir():
            roots.append(extra)
        local_models = Path(settings.DATA_DIR) / "models"
        if local_models.is_dir():
            roots.append(local_models)
        return roots

    def scan_roots(self, roots: Optional[list[str]] = None, max_depth: int = 2) -> list[dict]:
        """Walk candidate roots and return model-like subdirectories.

        Returns entries shaped like :func:`list_models` but for *discovered*
        (not yet imported) directories. Already-registered paths are marked
        ``registered=True`` so the UI can hide them.
        """
        search_roots: list[Path]
        if roots:
            search_roots = [Path(r) for r in roots if r]
        else:
            search_roots = self.default_scan_roots()

        registered_paths = {Path(m["path"]).resolve() for m in self.list_models() if m.get("path")}
        seen: set[Path] = set()
        results: list[dict] = []

        def _walk(root: Path, depth: int) -> None:
            if depth > max_depth or not root.is_dir():
                return
            # Is this directory itself a model?
            try:
                resolved = root.resolve()
            except OSError:
                return
            if resolved in seen:
                return
            seen.add(resolved)
            t = detect_model_type(root)
            if t:
                base_path = read_lora_base_path(root) if t == MODEL_TYPE_LORA else ""
                results.append(
                    {
                        "path": str(resolved),
                        "name": root.name,
                        "type": t,
                        "base_path": base_path,
                        "registered": resolved in registered_paths,
                    }
                )
                # Don't descend further; nested model dirs would be noise
                return
            # Otherwise descend (checkpoints/ etc.)
            for child in root.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    _walk(child, depth + 1)

        for r in search_roots:
            _walk(r, 0)

        results.sort(key=lambda m: (m["registered"], m["name"].lower()))
        return results

    # ------------------------------------------------------------------
    def _autoseed_trained_lora(self) -> None:
        """On first run, if the known training-pipeline output dir exists,
        pre-register it so the user finds their already-trained model."""
        with self._lock:
            data = self._read()
            if data.get("models"):
                return
        candidate = Path("D:/WeChatAI_models/output/lora")
        if not candidate.is_dir() or detect_model_type(candidate) != MODEL_TYPE_LORA:
            return
        try:
            self.add_model(
                path=str(candidate),
                name="我的微信分身 (LoRA)",
                notes="由训练流水线自动生成",
            )
        except Exception:
            # Best effort — never fail startup over this.
            pass


# Module-level singleton
registry = ModelRegistry()
