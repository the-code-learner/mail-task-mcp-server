#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
from huggingface_hub import hf_hub_download
from model2vec import StaticModel
from model2vec.quantization import quantize_and_reduce_dim
from safetensors.numpy import load_file
from tokenizers import Tokenizer

SOURCE_MODEL = os.getenv(
    "CONTEXT_MODEL_SOURCE_ID", "sentence-transformers/static-similarity-mrl-multilingual-v1"
).strip()
SOURCE_REVISION = os.getenv(
    "CONTEXT_MODEL_SOURCE_REVISION", "bb98c751b8b9d3b8b9d43e8836ed0b659cef9a05"
).strip()
SOURCE_SUBFOLDER = "0_StaticEmbedding"
TARGET_DIMS = int(os.getenv("CONTEXT_MODEL_DIMS", "128"))
TARGET_PATH = Path(
    os.getenv("CONTEXT_MODEL_PATH", "/data/models/static-similarity-mrl-multilingual-int8-128d")
)


def ready(path: Path) -> bool:
    return (path / "model.safetensors").is_file() and (path / "tokenizer.json").is_file()


def main() -> None:
    if ready(TARGET_PATH):
        print(f"Context model already present: {TARGET_PATH}")
        return
    if TARGET_DIMS != 128:
        raise SystemExit("v9 runtime currently validates only CONTEXT_MODEL_DIMS=128")

    TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    work_parent = TARGET_PATH.parent
    work_dir = Path(tempfile.mkdtemp(prefix=".context-model-build-", dir=work_parent))
    hf_cache = work_dir / "hf-cache"
    output_dir = work_dir / "model"
    try:
        print(f"Preparing {SOURCE_MODEL}@{SOURCE_REVISION} -> {TARGET_DIMS}d int8")
        weights_path = hf_hub_download(
            SOURCE_MODEL,
            "model.safetensors",
            subfolder=SOURCE_SUBFOLDER,
            revision=SOURCE_REVISION,
            cache_dir=hf_cache,
        )
        tokenizer_path = hf_hub_download(
            SOURCE_MODEL,
            "tokenizer.json",
            subfolder=SOURCE_SUBFOLDER,
            revision=SOURCE_REVISION,
            cache_dir=hf_cache,
        )
        state = load_file(weights_path)
        key = "embedding.weight" if "embedding.weight" in state else "embeddings"
        if key not in state:
            raise RuntimeError(f"Static embedding weights not found; keys={sorted(state)}")
        vectors = np.asarray(state[key], dtype=np.float32)
        tokenizer = Tokenizer.from_file(tokenizer_path)
        if vectors.shape[0] != tokenizer.get_vocab_size():
            raise RuntimeError(
                f"Vocabulary mismatch: vectors={vectors.shape[0]} tokenizer={tokenizer.get_vocab_size()}"
            )

        compact = quantize_and_reduce_dim(vectors, quantize_to="int8", dimensionality=TARGET_DIMS)
        model = StaticModel(
            compact,
            tokenizer,
            normalize=True,
            base_model_name=SOURCE_MODEL,
            language=["multilingual", "it", "en"],
            config={
                "normalize": True,
                "source_model": SOURCE_MODEL,
                "source_revision": SOURCE_REVISION,
                "source_license": "Apache-2.0",
                "compression": "matryoshka-prefix-128d+model2vec-int8",
                "contains_user_data": False,
            },
        )
        model.save_pretrained(output_dir, model_name="nomadcompass-static-mrl-multilingual-128d-int8")
        metadata = {
            "source_model": SOURCE_MODEL,
            "source_revision": SOURCE_REVISION,
            "source_license": "Apache-2.0",
            "dims": TARGET_DIMS,
            "quantization": "int8",
            "contains_user_data": False,
        }
        (output_dir / "nomadcompass-model.json").write_text(json.dumps(metadata, indent=2) + "\n")

        # Sanity-check through the exact runtime loader before making the model current.
        runtime = StaticModel.from_pretrained(output_dir, normalize=True, force_download=False)
        probe = runtime.encode(["memoria persistente del progetto", "persistent project memory"])
        if probe.shape != (2, TARGET_DIMS) or not np.isfinite(probe).all():
            raise RuntimeError(f"Unexpected runtime probe shape/content: {probe.shape}")

        if TARGET_PATH.exists():
            shutil.rmtree(TARGET_PATH)
        os.replace(output_dir, TARGET_PATH)
        print(f"Context model ready: {TARGET_PATH}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
