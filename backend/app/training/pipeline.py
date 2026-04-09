"""One-click training pipeline: export → install deps → download model → train → serve.

Optimized for RTX 3070 (8GB VRAM) + 48GB RAM.
Uses subprocess.Popen in threads (Windows asyncio doesn't support create_subprocess_exec).
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import traceback
from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config.settings import get_settings
from app.api.ws import ws_manager


class PipelineStage(str, Enum):
    IDLE = "idle"
    EXPORTING = "exporting"
    INSTALLING = "installing"
    DOWNLOADING = "downloading"
    TRAINING = "training"
    STARTING_SERVER = "starting_server"
    DONE = "done"
    FAILED = "failed"


BASE_MODEL = "Qwen/Qwen2.5-3B-Instruct"
MODEL_SHORT = "Qwen2.5-3B-Instruct"


def _run_cmd(cmd: list[str], env=None, timeout=None) -> tuple[int, str]:
    """Run a command and return (returncode, combined stdout+stderr)."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env or os.environ.copy(),
    )
    output, _ = proc.communicate(timeout=timeout)
    return proc.returncode, output.decode("utf-8", errors="ignore")


def _run_cmd_stream(cmd: list[str], env=None):
    """Run a command and yield output lines."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env or os.environ.copy(),
        bufsize=1,
    )
    for line in iter(proc.stdout.readline, b""):
        yield line.decode("utf-8", errors="ignore").rstrip()
    proc.wait()
    yield f"__EXIT_CODE__:{proc.returncode}"


class TrainingPipeline:
    def __init__(self):
        self.stage = PipelineStage.IDLE
        self.progress = 0
        self.message = ""
        self.error = ""
        self.inference_process: Optional[subprocess.Popen] = None
        self.inference_port = 8090
        self._running = False
        self._cancel = False

        settings = get_settings()
        self.data_dir = Path(settings.DATA_DIR)
        self.training_dir = self.data_dir / "training"
        self.models_dir = Path("D:/WeChatAI_models")
        self.output_dir = self.models_dir / "output"

    def get_status(self) -> dict:
        is_running = self.inference_process is not None and self.inference_process.poll() is None
        return {
            "stage": self.stage.value,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "inference_running": is_running,
            "inference_url": f"http://localhost:{self.inference_port}/v1" if is_running else "",
        }

    async def run_full_pipeline(self, db):
        if self._running:
            return
        self._running = True
        self._cancel = False
        self.error = ""

        try:
            # Stage 1: Export data
            await self._update(PipelineStage.EXPORTING, 0, "正在导出聊天记录...")
            from app.training.data_exporter import export_training_data
            result = await export_training_data(db)
            if result["total_conversations"] < 5:
                raise ValueError(f"训练数据不足: 仅 {result['total_conversations']} 段对话")
            await self._update(PipelineStage.EXPORTING, 100,
                f"导出完成: {result['total_conversations']} 段对话")
            if self._cancel: return

            # Stage 2: Install deps
            await self._update(PipelineStage.INSTALLING, 0, "正在检查依赖...")
            await asyncio.to_thread(self._install_deps_sync)
            if self._cancel: return

            # Stage 3: Download model
            await self._update(PipelineStage.DOWNLOADING, 0, f"正在下载 {MODEL_SHORT}...")
            model_path = await asyncio.to_thread(self._download_model_sync)
            if self._cancel or not model_path: return

            # Stage 4: Train
            await self._update(PipelineStage.TRAINING, 0, "正在启动 QLoRA 4bit 训练...")
            await self._run_training_async(model_path)
            if self._cancel: return

            # Stage 5: Start inference
            await self._update(PipelineStage.STARTING_SERVER, 0, "正在启动推理服务...")
            await self._start_inference_async(model_path)

            await self._update(PipelineStage.DONE, 100,
                f"🎉 分身模型已部署! API: http://localhost:{self.inference_port}/v1")

        except Exception as e:
            err_msg = str(e) or traceback.format_exc()[-300:]
            logger.error(f"Pipeline failed: {err_msg}")
            self.error = err_msg
            await self._update(PipelineStage.FAILED, self.progress, f"失败: {err_msg}")
        finally:
            self._running = False

    async def _update(self, stage: PipelineStage, progress: int, message: str):
        self.stage = stage
        self.progress = progress
        self.message = message
        logger.info(f"[{stage.value}] {progress}% {message}")
        try:
            await ws_manager.broadcast("training_progress", self.get_status())
        except:
            pass

    # ==================== INSTALL ====================

    def _install_deps_sync(self):
        """Check and install dependencies (runs in thread)."""
        missing = []
        for pkg in ["llamafactory", "bitsandbytes", "peft", "accelerate"]:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)

        if not missing:
            self._update_sync(PipelineStage.INSTALLING, 100, "所有依赖已就绪")
            return

        self._update_sync(PipelineStage.INSTALLING, 10, f"安装中: {', '.join(missing)}")

        packages = []
        if "llamafactory" in missing:
            packages.append("llamafactory")
        if "bitsandbytes" in missing:
            packages.append("bitsandbytes")

        code, output = _run_cmd(
            [sys.executable, "-m", "pip", "install"] + packages,
            timeout=600,
        )
        if code != 0:
            raise RuntimeError(f"依赖安装失败:\n{output[-300:]}")

        self._update_sync(PipelineStage.INSTALLING, 100, "依赖安装完成")

    # ==================== DOWNLOAD ====================

    def _download_model_sync(self) -> str:
        """Download base model (runs in thread)."""
        self.models_dir.mkdir(parents=True, exist_ok=True)
        model_dir = self.models_dir / MODEL_SHORT

        if model_dir.exists() and any(model_dir.glob("*.safetensors")):
            self._update_sync(PipelineStage.DOWNLOADING, 100, f"{MODEL_SHORT} 已就绪")
            return str(model_dir)

        self._update_sync(PipelineStage.DOWNLOADING, 10, f"下载 {MODEL_SHORT} (约6GB)...")

        # Ensure huggingface_hub is installed
        try:
            import huggingface_hub
        except ImportError:
            _run_cmd([sys.executable, "-m", "pip", "install", "huggingface_hub"], timeout=120)

        code, output = _run_cmd(
            [sys.executable, "-c",
             f"from huggingface_hub import snapshot_download; "
             f"snapshot_download('{BASE_MODEL}', local_dir=r'{model_dir}')"],
            timeout=1800,
        )

        if code != 0:
            raise RuntimeError(f"模型下载失败:\n{output[-300:]}")

        if not any(model_dir.glob("*.safetensors")):
            raise RuntimeError(f"模型文件未找到: {model_dir}")

        self._update_sync(PipelineStage.DOWNLOADING, 100, f"{MODEL_SHORT} 下载完成")
        return str(model_dir)

    # ==================== TRAINING ====================

    async def _run_training_async(self, model_path: str):
        """Run QLoRA training, streaming progress."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        lora_dir = self.output_dir / "lora"
        self.training_dir.mkdir(parents=True, exist_ok=True)

        # Write dataset_info.json for LLaMA-Factory
        dataset_info = {
            "my_wechat_style": {
                "file_name": "my_wechat_style.json",
                "formatting": "sharegpt",
                "columns": {"messages": "conversations"},
            }
        }
        (self.training_dir / "dataset_info.json").write_text(
            json.dumps(dataset_info, indent=2), encoding="utf-8"
        )

        # Training config
        config = {
            "stage": "sft",
            "model_name_or_path": model_path,
            "dataset": "my_wechat_style",
            "dataset_dir": str(self.training_dir).replace("\\", "/"),
            "template": "qwen",
            "finetuning_type": "lora",
            "lora_rank": 8,
            "lora_alpha": 16,
            "lora_target": "all",
            "quantization_bit": 4,
            "quantization_method": "bitsandbytes",
            "cutoff_len": 256,
            "per_device_train_batch_size": 1,
            "gradient_accumulation_steps": 8,
            "num_train_epochs": 3,
            "learning_rate": 2e-4,
            "lr_scheduler_type": "cosine",
            "warmup_ratio": 0.1,
            "output_dir": str(lora_dir).replace("\\", "/"),
            "fp16": True,
            "logging_steps": 5,
            "save_steps": 500,
            "overwrite_output_dir": True,
            "gradient_checkpointing": True,
            "optim": "paged_adamw_8bit",
            "max_grad_norm": 1.0,
            "report_to": "none",
        }

        config_file = self.training_dir / "train_config.json"
        config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")

        wrapper = Path(__file__).parent / "train_wrapper.py"
        cmd = [sys.executable, str(wrapper), "train", str(config_file)]
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = "0"

        logger.info(f"Training cmd: {' '.join(cmd)}")

        # Run in thread, poll for output
        def train_thread():
            results = []
            for line in _run_cmd_stream(cmd, env=env):
                results.append(line)
            return results

        train_future = asyncio.get_event_loop().run_in_executor(None, train_thread)

        # Poll the output directory for progress
        while not train_future.done():
            await asyncio.sleep(5)
            # Check for log files
            log_file = lora_dir / "trainer_log.jsonl"
            if log_file.exists():
                try:
                    lines = log_file.read_text(errors="ignore").strip().split("\n")
                    if lines:
                        last = json.loads(lines[-1])
                        step = last.get("current_steps", 0)
                        total = last.get("total_steps", 1)
                        loss = last.get("loss", 0)
                        pct = min(99, int(step / max(total, 1) * 100))
                        await self._update(PipelineStage.TRAINING, pct,
                            f"训练中 Step {step}/{total}, loss={loss:.3f}")
                except:
                    pass
            if self._cancel:
                break

        output_lines = await train_future

        # Check exit code
        exit_code = 0
        for line in reversed(output_lines):
            if line.startswith("__EXIT_CODE__:"):
                exit_code = int(line.split(":")[1])
                break

        if exit_code != 0:
            error_lines = [l for l in output_lines if "error" in l.lower() or "Error" in l]
            error_msg = "\n".join(error_lines[-3:]) if error_lines else "未知错误"
            raise RuntimeError(f"训练失败 (exit {exit_code}): {error_msg}")

        # Verify output
        if not any(lora_dir.glob("adapter_*")):
            raise RuntimeError(f"训练输出未找到: {lora_dir}")

        await self._update(PipelineStage.TRAINING, 100, "训练完成!")

    # ==================== INFERENCE ====================

    async def _start_inference_async(self, base_model_path: str):
        """Start inference server."""
        lora_dir = self.output_dir / "lora"

        # Config for LLaMA-Factory API
        config = {
            "model_name_or_path": base_model_path,
            "adapter_name_or_path": str(lora_dir).replace("\\", "/"),
            "template": "qwen",
            "finetuning_type": "lora",
            "quantization_bit": 4,
            "quantization_method": "bitsandbytes",
        }

        config_file = self.training_dir / "inference_config.json"
        config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")

        wrapper = Path(__file__).parent / "train_wrapper.py"
        cmd = [
            sys.executable, str(wrapper), "api",
            str(config_file),
        ]
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = "0"
        env["API_PORT"] = str(self.inference_port)

        logger.info(f"Starting inference: {' '.join(cmd)}")
        self.inference_process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )

        # Wait for server
        import httpx
        for i in range(60):
            await asyncio.sleep(2)
            if self.inference_process.poll() is not None:
                raise RuntimeError("推理服务启动后立即退出")
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"http://localhost:{self.inference_port}/v1/models", timeout=3
                    )
                    if resp.status_code == 200:
                        await self._update(PipelineStage.STARTING_SERVER, 100, "推理服务已就绪")
                        return
            except:
                pass
            await self._update(PipelineStage.STARTING_SERVER, min(90, i * 2),
                f"等待服务启动... ({i*2}秒)")

        raise RuntimeError("推理服务启动超时")

    async def stop_inference(self):
        if self.inference_process:
            self.inference_process.terminate()
            try:
                self.inference_process.wait(timeout=10)
            except:
                self.inference_process.kill()
            self.inference_process = None
            await self._update(PipelineStage.IDLE, 0, "推理服务已停止")

    def stop_training(self):
        self._cancel = True
        self._running = False
        self.stage = PipelineStage.IDLE
        self.message = "已取消"

    def _update_sync(self, stage: PipelineStage, progress: int, message: str):
        """Synchronous update (for thread contexts)."""
        self.stage = stage
        self.progress = progress
        self.message = message
        logger.info(f"[{stage.value}] {progress}% {message}")


pipeline = TrainingPipeline()
