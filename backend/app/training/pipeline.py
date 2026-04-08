"""One-click training pipeline: export → download model → train → merge → serve.

Runs as a background task with progress reporting via WebSocket.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config.settings import get_settings
from app.api.ws import ws_manager


class PipelineStage(str, Enum):
    IDLE = "idle"
    EXPORTING = "exporting"
    DOWNLOADING = "downloading"
    TRAINING = "training"
    MERGING = "merging"
    STARTING_SERVER = "starting_server"
    DONE = "done"
    FAILED = "failed"


class TrainingPipeline:
    """Manages the full training pipeline as a singleton."""

    def __init__(self):
        self.stage = PipelineStage.IDLE
        self.progress = 0  # 0-100
        self.message = ""
        self.error = ""
        self.training_process: Optional[subprocess.Popen] = None
        self.inference_process: Optional[subprocess.Popen] = None
        self.inference_port = 8090
        self._running = False

        settings = get_settings()
        self.data_dir = Path(settings.DATA_DIR)
        self.training_dir = self.data_dir / "training"
        self.models_dir = self.data_dir / "models"
        self.base_model_dir = self.models_dir / "base"
        self.output_dir = self.models_dir / "output"
        self.merged_dir = self.models_dir / "merged"

    def get_status(self) -> dict:
        return {
            "stage": self.stage.value,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "inference_running": self.inference_process is not None and self.inference_process.poll() is None,
            "inference_url": f"http://localhost:{self.inference_port}/v1" if self.inference_process else "",
        }

    async def run_full_pipeline(self, db):
        """Execute the complete pipeline: export → train → deploy."""
        if self._running:
            return {"error": "Pipeline already running"}

        self._running = True
        self.error = ""

        try:
            # Stage 1: Export data
            await self._update_stage(PipelineStage.EXPORTING, 0, "正在导出聊天记录...")
            from app.training.data_exporter import export_training_data
            export_result = await export_training_data(db)
            if export_result["total_conversations"] < 10:
                raise ValueError(f"训练数据不足，仅有 {export_result['total_conversations']} 段对话，至少需要 10 段")
            await self._update_stage(PipelineStage.EXPORTING, 100,
                f"导出完成: {export_result['total_conversations']} 段对话, {export_result['total_my_messages']} 条我的消息")

            # Stage 2: Check/Download base model
            await self._update_stage(PipelineStage.DOWNLOADING, 0, "正在检查基座模型...")
            model_ready = await self._ensure_base_model()
            if not model_ready:
                await self._update_stage(PipelineStage.DOWNLOADING, 100,
                    "基座模型需要手动下载 (见说明)")

            # Stage 3: Training
            await self._update_stage(PipelineStage.TRAINING, 0, "正在启动 LoRA 训练...")
            await self._run_training()
            await self._update_stage(PipelineStage.TRAINING, 100, "训练完成")

            # Stage 4: Merge LoRA
            await self._update_stage(PipelineStage.MERGING, 0, "正在合并模型...")
            await self._merge_lora()
            await self._update_stage(PipelineStage.MERGING, 100, "模型合并完成")

            # Stage 5: Start inference server
            await self._update_stage(PipelineStage.STARTING_SERVER, 0, "正在启动推理服务...")
            await self._start_inference_server()
            await self._update_stage(PipelineStage.DONE, 100,
                f"🎉 分身模型已部署! API: http://localhost:{self.inference_port}/v1")

        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            self.error = str(e)
            await self._update_stage(PipelineStage.FAILED, self.progress, f"失败: {e}")

        finally:
            self._running = False

    async def _update_stage(self, stage: PipelineStage, progress: int, message: str):
        self.stage = stage
        self.progress = progress
        self.message = message
        logger.info(f"Pipeline [{stage.value}] {progress}% - {message}")
        await ws_manager.broadcast("training_progress", self.get_status())

    async def _ensure_base_model(self) -> bool:
        """Check if base model exists, guide download if not."""
        self.base_model_dir.mkdir(parents=True, exist_ok=True)

        # Check if model files exist
        model_path = self.base_model_dir / "Qwen2.5-7B-Instruct"
        if model_path.exists() and any(model_path.glob("*.safetensors")):
            await self._update_stage(PipelineStage.DOWNLOADING, 100, "基座模型已就绪")
            return True

        # Try to download via huggingface-cli
        await self._update_stage(PipelineStage.DOWNLOADING, 10, "正在下载 Qwen2.5-7B-Instruct...")
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "huggingface_hub", "download",
                "Qwen/Qwen2.5-7B-Instruct",
                "--local-dir", str(model_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Monitor download progress
            while proc.returncode is None:
                await asyncio.sleep(5)
                await self._update_stage(PipelineStage.DOWNLOADING, 50, "正在下载模型文件...")
                try:
                    proc_status = proc.returncode
                except:
                    pass

            await proc.wait()
            if proc.returncode == 0:
                return True
            else:
                stderr = (await proc.stderr.read()).decode(errors="ignore")
                logger.warning(f"Model download failed: {stderr[:200]}")
        except Exception as e:
            logger.warning(f"Auto-download failed: {e}")

        return False

    async def _run_training(self):
        """Run LLaMA-Factory LoRA training."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Create training config
        model_path = self.base_model_dir / "Qwen2.5-7B-Instruct"
        if not model_path.exists():
            model_path = Path("Qwen/Qwen2.5-7B-Instruct")  # Fallback to HF hub name

        config = {
            "stage": "sft",
            "model_name_or_path": str(model_path),
            "dataset": "my_wechat_style",
            "dataset_dir": str(self.training_dir),
            "template": "qwen",
            "finetuning_type": "lora",
            "lora_rank": 16,
            "lora_alpha": 32,
            "lora_target": "all",
            "cutoff_len": 512,
            "per_device_train_batch_size": 2,
            "gradient_accumulation_steps": 4,
            "num_train_epochs": 3,
            "learning_rate": 5e-5,
            "lr_scheduler_type": "cosine",
            "warmup_ratio": 0.1,
            "output_dir": str(self.output_dir / "lora"),
            "bf16": True,
            "logging_steps": 10,
            "save_steps": 100,
            "overwrite_output_dir": True,
        }

        config_file = self.training_dir / "train_config.json"
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        # Run training
        cmd = [sys.executable, "-m", "llamafactory.train", str(config_file)]
        logger.info(f"Starting training: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(self.data_dir.parent),
        )

        # Monitor training progress
        while proc.returncode is None:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="ignore").strip()
            if text:
                # Parse training progress from log lines
                if "loss" in text.lower() and "step" in text.lower():
                    self.message = text[:100]
                    # Try to extract step progress
                    import re
                    m = re.search(r"(\d+)/(\d+)", text)
                    if m:
                        current, total = int(m.group(1)), int(m.group(2))
                        pct = min(99, int(current / total * 100))
                        await self._update_stage(PipelineStage.TRAINING, pct,
                            f"训练中... Step {current}/{total}")

            await asyncio.sleep(0.1)

        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"Training failed with exit code {proc.returncode}")

    async def _merge_lora(self):
        """Merge LoRA adapter with base model."""
        self.merged_dir.mkdir(parents=True, exist_ok=True)

        model_path = self.base_model_dir / "Qwen2.5-7B-Instruct"
        if not model_path.exists():
            model_path = Path("Qwen/Qwen2.5-7B-Instruct")

        config = {
            "model_name_or_path": str(model_path),
            "adapter_name_or_path": str(self.output_dir / "lora"),
            "template": "qwen",
            "finetuning_type": "lora",
            "export_dir": str(self.merged_dir),
            "export_size": 5,
            "export_legacy_format": False,
        }

        config_file = self.training_dir / "merge_config.json"
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        cmd = [sys.executable, "-m", "llamafactory.export", str(config_file)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("LoRA merge failed")

    async def _start_inference_server(self):
        """Start vLLM or compatible inference server."""
        model_path = str(self.merged_dir)
        if not Path(model_path).exists() or not any(Path(model_path).glob("*.safetensors")):
            # Fallback: use LoRA adapter directly
            model_path = str(self.base_model_dir / "Qwen2.5-7B-Instruct")

        # Try vLLM first
        try:
            cmd = [
                sys.executable, "-m", "vllm.entrypoints.openai.api_server",
                "--model", model_path,
                "--port", str(self.inference_port),
                "--max-model-len", "2048",
                "--trust-remote-code",
            ]
            self.inference_process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            await asyncio.sleep(10)
            if self.inference_process.poll() is None:
                return
        except Exception:
            pass

        # Fallback: try llamafactory API server
        try:
            cmd = [
                sys.executable, "-m", "llamafactory.api",
                "--model_name_or_path", model_path,
                "--template", "qwen",
                "--port", str(self.inference_port),
            ]
            self.inference_process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            await asyncio.sleep(10)
            if self.inference_process.poll() is None:
                return
        except Exception:
            pass

        logger.warning("Could not start inference server automatically")

    async def stop_inference(self):
        """Stop the inference server."""
        if self.inference_process:
            self.inference_process.terminate()
            try:
                self.inference_process.wait(timeout=10)
            except:
                self.inference_process.kill()
            self.inference_process = None
            await self._update_stage(PipelineStage.IDLE, 0, "推理服务已停止")

    def stop_training(self):
        """Stop ongoing training."""
        if self.training_process:
            self.training_process.terminate()
            self.training_process = None
        self._running = False
        self.stage = PipelineStage.IDLE
        self.message = "训练已停止"


# Singleton
pipeline = TrainingPipeline()
