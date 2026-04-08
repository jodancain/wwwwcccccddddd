"""One-click training pipeline: export → install deps → download model → train → serve.

Optimized for RTX 3070 (8GB VRAM) + 48GB RAM:
- Uses Qwen2.5-3B-Instruct (fits in 8GB with QLoRA 4bit)
- QLoRA with 4bit quantization (~5GB VRAM during training)
- llama.cpp GGUF for inference (~3GB VRAM / CPU only)
"""
import asyncio
import json
import os
import re
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
    INSTALLING = "installing"
    DOWNLOADING = "downloading"
    TRAINING = "training"
    CONVERTING = "converting"
    STARTING_SERVER = "starting_server"
    DONE = "done"
    FAILED = "failed"


# Model optimized for 8GB VRAM
BASE_MODEL = "Qwen/Qwen2.5-3B-Instruct"
MODEL_SHORT = "Qwen2.5-3B-Instruct"


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
        self.models_dir = Path("D:/WeChatAI_models")  # Use D: drive for space
        self.output_dir = self.models_dir / "output"
        self.merged_dir = self.models_dir / "merged"

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
                f"导出完成: {result['total_conversations']} 段对话, {result['total_my_messages']} 条消息")
            if self._cancel: return

            # Stage 2: Install dependencies
            await self._update(PipelineStage.INSTALLING, 0, "正在检查依赖...")
            await self._install_deps()
            if self._cancel: return

            # Stage 3: Download model
            await self._update(PipelineStage.DOWNLOADING, 0, f"正在下载 {MODEL_SHORT}...")
            model_path = await self._download_model()
            if self._cancel: return

            # Stage 4: QLoRA Training
            await self._update(PipelineStage.TRAINING, 0, "正在启动 QLoRA 4bit 训练...")
            await self._run_training(model_path)
            if self._cancel: return

            # Stage 5: Start inference
            await self._update(PipelineStage.STARTING_SERVER, 0, "正在启动推理服务...")
            await self._start_inference(model_path)

            await self._update(PipelineStage.DONE, 100,
                f"🎉 分身模型已部署! API: http://localhost:{self.inference_port}/v1")

        except Exception as e:
            import traceback
            err_msg = str(e) or traceback.format_exc()[-200:]
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
        await ws_manager.broadcast("training_progress", self.get_status())

    async def _install_deps(self):
        """Install LLaMA-Factory and dependencies for QLoRA."""
        # Check what's already installed
        missing = []
        for pkg in ["llamafactory", "bitsandbytes", "peft", "accelerate"]:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)

        if not missing:
            await self._update(PipelineStage.INSTALLING, 100, "所有依赖已就绪")
            return

        # Install everything in one pip call (llamafactory pulls most deps)
        packages = ["llamafactory"]
        if "bitsandbytes" in missing:
            packages.append("bitsandbytes")

        await self._update(PipelineStage.INSTALLING, 10,
            f"正在安装依赖 (可能需要几分钟)...")

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", *packages,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        # Stream output to track progress
        line_count = 0
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="ignore").strip()
            line_count += 1
            if text and ("Collecting" in text or "Installing" in text or "Successfully" in text):
                pct = min(90, 10 + line_count)
                await self._update(PipelineStage.INSTALLING, pct, text[:80])
            if self._cancel:
                proc.terminate()
                return

        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("依赖安装失败，请手动运行: pip install llamafactory bitsandbytes")

        await self._update(PipelineStage.INSTALLING, 100, "依赖安装完成")

    async def _download_model(self) -> str:
        """Download base model to D: drive."""
        self.models_dir.mkdir(parents=True, exist_ok=True)
        model_dir = self.models_dir / MODEL_SHORT

        # Check if already downloaded
        if model_dir.exists() and any(model_dir.glob("*.safetensors")):
            await self._update(PipelineStage.DOWNLOADING, 100, f"{MODEL_SHORT} 已就绪")
            return str(model_dir)

        await self._update(PipelineStage.DOWNLOADING, 10, f"正在从 HuggingFace 下载 {MODEL_SHORT}...")

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c",
                f"from huggingface_hub import snapshot_download; "
                f"snapshot_download('{BASE_MODEL}', local_dir=r'{model_dir}')",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )

            while proc.returncode is None:
                await asyncio.sleep(3)
                # Check download progress by file count
                if model_dir.exists():
                    files = list(model_dir.rglob("*"))
                    pct = min(90, len(files) * 5)
                    await self._update(PipelineStage.DOWNLOADING, pct,
                        f"正在下载... ({len(files)} 个文件)")
                if self._cancel:
                    proc.terminate()
                    return ""

            await proc.wait()
            if proc.returncode != 0:
                output = (await proc.stdout.read()).decode(errors="ignore")
                raise RuntimeError(f"模型下载失败: {output[-200:]}")

        except ImportError:
            # Install huggingface_hub first
            pip = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "huggingface_hub", "--quiet",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            await pip.wait()
            # Retry
            return await self._download_model()

        await self._update(PipelineStage.DOWNLOADING, 100, f"{MODEL_SHORT} 下载完成")
        return str(model_dir)

    async def _run_training(self, model_path: str):
        """Run QLoRA 4bit training optimized for RTX 3070 (8GB VRAM)."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        lora_dir = self.output_dir / "lora"

        # Write training config for QLoRA 4bit
        config = {
            "stage": "sft",
            "model_name_or_path": model_path,
            "dataset": "my_wechat_style",
            "dataset_dir": str(self.training_dir),
            "template": "qwen",
            "finetuning_type": "lora",
            "lora_rank": 8,
            "lora_alpha": 16,
            "lora_target": "all",
            # QLoRA 4bit quantization
            "quantization_bit": 4,
            "quantization_method": "bitsandbytes",
            # Memory optimization for 8GB VRAM
            "cutoff_len": 256,
            "per_device_train_batch_size": 1,
            "gradient_accumulation_steps": 8,
            "num_train_epochs": 3,
            "learning_rate": 2e-4,
            "lr_scheduler_type": "cosine",
            "warmup_ratio": 0.1,
            "output_dir": str(lora_dir),
            "fp16": True,
            "logging_steps": 5,
            "save_steps": 200,
            "overwrite_output_dir": True,
            "gradient_checkpointing": True,
            "optim": "paged_adamw_8bit",
            "max_grad_norm": 1.0,
        }

        config_file = self.training_dir / "train_config.json"
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        # Use llamafactory CLI
        cmd = [sys.executable, "-m", "llamafactory", "train", str(config_file)]
        logger.info(f"Training cmd: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "0"},
        )

        last_step = 0
        total_steps = 0

        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="ignore").strip()
            if not text:
                continue

            # Parse progress
            step_match = re.search(r"(\d+)/(\d+)", text)
            loss_match = re.search(r"loss['\"]?\s*[:=]\s*([\d.]+)", text, re.IGNORECASE)

            if step_match:
                current = int(step_match.group(1))
                total_steps = int(step_match.group(2))
                pct = min(99, int(current / total_steps * 100))
                loss_str = f", loss={loss_match.group(1)}" if loss_match else ""
                await self._update(PipelineStage.TRAINING, pct,
                    f"训练中 Step {current}/{total_steps}{loss_str}")
                last_step = current

            if self._cancel:
                proc.terminate()
                raise RuntimeError("训练被用户取消")

        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"训练失败 (exit code {proc.returncode})")

        await self._update(PipelineStage.TRAINING, 100, "训练完成")

    async def _start_inference(self, base_model_path: str):
        """Start inference server using LLaMA-Factory API (with LoRA adapter)."""
        lora_dir = self.output_dir / "lora"

        # LLaMA-Factory API server with LoRA adapter
        config = {
            "model_name_or_path": base_model_path,
            "adapter_name_or_path": str(lora_dir),
            "template": "qwen",
            "finetuning_type": "lora",
            "quantization_bit": 4,
            "quantization_method": "bitsandbytes",
        }

        config_file = self.training_dir / "inference_config.json"
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        cmd = [
            sys.executable, "-m", "llamafactory", "api",
            str(config_file),
            "--port", str(self.inference_port),
        ]

        logger.info(f"Starting inference: {' '.join(cmd)}")
        self.inference_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "0"},
        )

        # Wait for server to be ready
        import httpx
        for i in range(30):
            await asyncio.sleep(2)
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"http://localhost:{self.inference_port}/v1/models", timeout=3)
                    if resp.status_code == 200:
                        await self._update(PipelineStage.STARTING_SERVER, 100, "推理服务已就绪")
                        return
            except:
                pass
            await self._update(PipelineStage.STARTING_SERVER, min(90, i * 3),
                f"等待服务启动... ({i*2}秒)")

        if self.inference_process.poll() is not None:
            raise RuntimeError("推理服务启动失败")

    async def stop_inference(self):
        if self.inference_process:
            self.inference_process.terminate()
            try: self.inference_process.wait(timeout=10)
            except: self.inference_process.kill()
            self.inference_process = None
            await self._update(PipelineStage.IDLE, 0, "推理服务已停止")

    def stop_training(self):
        self._cancel = True
        self._running = False
        self.stage = PipelineStage.IDLE
        self.message = "已取消"


pipeline = TrainingPipeline()
