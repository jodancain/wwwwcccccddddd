"""Simple OpenAI-compatible API server for the fine-tuned model.

Uses FastAPI + transformers + peft. No LLaMA-Factory dependency.
"""
import argparse
import io
import json
import os
import sys
import time
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import torch
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel


app = FastAPI(title="WeChatAI Personal Model")
model = None
tokenizer = None
model_name = "my-style"


class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str = "my-style"
    messages: list[ChatMessage]
    temperature: float = 0.7
    max_tokens: int = 512
    stream: bool = False

class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list


@app.get("/v1/models")
def list_models():
    return {"data": [{"id": model_name, "object": "model"}]}


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=req.max_tokens,
            temperature=max(req.temperature, 0.01),
            do_sample=True,
            top_p=0.9,
            pad_token_id=tokenizer.pad_token_id,
        )

    new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
    response_text = tokenizer.decode(new_tokens, skip_special_tokens=True)

    return ChatResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:8]}",
        created=int(time.time()),
        model=model_name,
        choices=[{
            "index": 0,
            "message": {"role": "assistant", "content": response_text},
            "finish_reason": "stop",
        }],
    )


def main():
    global model, tokenizer, model_name

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Base / full model path")
    parser.add_argument("--lora", default="", help="Optional LoRA adapter path")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--name", default="", help="Display name exposed in /v1/models")
    args = parser.parse_args()

    if args.name:
        model_name = args.name

    # Prefer tokenizer shipped alongside the LoRA (training pipeline copies
    # chat_template etc. into the adapter dir). Fall back to the base model.
    tokenizer_path = args.lora or args.model
    print(f"Loading tokenizer from: {tokenizer_path}")
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"Loading base model: {args.model}")
    base_model = AutoModelForCausalLM.from_pretrained(
        args.model, torch_dtype=torch.float16, device_map="auto", trust_remote_code=True,
    )

    if args.lora:
        print(f"Loading LoRA adapter: {args.lora}")
        model = PeftModel.from_pretrained(base_model, args.lora)
    else:
        model = base_model
    model.eval()
    print(f"Model '{model_name}' loaded. Starting server on port {args.port}")

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
