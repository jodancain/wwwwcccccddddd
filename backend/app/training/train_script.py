"""Direct training script using transformers + peft (no LLaMA-Factory).

Compatible with Python 3.14 + RTX 3070 8GB.
Uses Qwen2.5-1.5B-Instruct with LoRA fp16.
"""
import io
import json
import os
import sys

# Force UTF-8 on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
os.environ["CUDA_VISIBLE_DEVICES"] = "0"

import torch
from datasets import Dataset
from transformers import AutoTokenizer, AutoModelForCausalLM, TrainingArguments, Trainer
from peft import LoraConfig, get_peft_model, TaskType


def main():
    if len(sys.argv) < 2:
        print("Usage: python train_script.py <config.json>")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        config = json.load(f)

    model_name = config["model_name_or_path"]
    data_file = os.path.join(config.get("dataset_dir", "."), config.get("dataset_file", "my_wechat_style.json"))
    output_dir = config["output_dir"]
    num_epochs = config.get("num_train_epochs", 3)
    batch_size = config.get("per_device_train_batch_size", 1)
    grad_accum = config.get("gradient_accumulation_steps", 8)
    lr = config.get("learning_rate", 2e-4)
    cutoff = config.get("cutoff_len", 256)
    lora_rank = config.get("lora_rank", 8)
    lora_alpha = config.get("lora_alpha", 16)

    # Load data
    print(f"Loading data from {data_file}...")
    with open(data_file, "r", encoding="utf-8") as f:
        raw = json.load(f)
    print(f"Conversations: {len(raw)}")

    # Load tokenizer
    print(f"Loading tokenizer: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Convert conversations to chat format
    texts = []
    for entry in raw:
        parts = []
        for turn in entry["conversations"]:
            role = "user" if turn["from"] == "human" else "assistant"
            parts.append({"role": role, "content": turn["value"]})
        try:
            text = tokenizer.apply_chat_template(parts, tokenize=False, add_generation_prompt=False)
            texts.append(text)
        except Exception:
            continue
    print(f"Formatted {len(texts)} training samples")

    # Tokenize
    def tokenize_fn(examples):
        out = tokenizer(examples["text"], truncation=True, max_length=cutoff, padding="max_length")
        out["labels"] = out["input_ids"].copy()
        return out

    ds = Dataset.from_dict({"text": texts})
    ds = ds.map(tokenize_fn, batched=True, remove_columns=["text"])
    print(f"Tokenized dataset: {len(ds)} samples")

    # Load model
    print(f"Loading model: {model_name}")
    model = AutoModelForCausalLM.from_pretrained(
        model_name, torch_dtype=torch.float16, device_map="auto", trust_remote_code=True,
    )
    model.enable_input_require_grads()

    # Apply LoRA
    lora_config = LoraConfig(
        r=lora_rank, lora_alpha=lora_alpha,
        target_modules="all-linear", task_type=TaskType.CAUSAL_LM,
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Training args
    total_steps = (len(ds) * num_epochs) // (batch_size * grad_accum)
    save_steps = max(total_steps // 3, 50)

    args = TrainingArguments(
        output_dir=output_dir,
        num_train_epochs=num_epochs,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=grad_accum,
        learning_rate=lr,
        fp16=True,
        logging_steps=5,
        save_steps=save_steps,
        overwrite_output_dir=True,
        gradient_checkpointing=True,
        optim="adamw_torch",
        report_to="none",
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        max_grad_norm=1.0,
    )

    # Train
    trainer = Trainer(model=model, args=args, train_dataset=ds, processing_class=tokenizer)
    print(f"Starting training: {total_steps} steps, {num_epochs} epochs")
    result = trainer.train()
    print(f"Training complete! Loss: {result.training_loss:.4f}")

    # Save LoRA adapter + tokenizer
    print(f"Saving to {output_dir}")
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)

    # Write training stats
    stats = {
        "loss": result.training_loss,
        "steps": result.global_step,
        "runtime": result.metrics.get("train_runtime", 0),
        "samples_per_second": result.metrics.get("train_samples_per_second", 0),
    }
    with open(os.path.join(output_dir, "train_stats.json"), "w") as f:
        json.dump(stats, f, indent=2)

    print("DONE!")


if __name__ == "__main__":
    main()
