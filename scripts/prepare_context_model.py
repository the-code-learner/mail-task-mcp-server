#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

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
MODEL_URL = os.getenv("CONTEXT_MODEL_URL", "").strip()
MODEL_SHA256 = os.getenv("CONTEXT_MODEL_SHA256", "").strip().lower()
AUTO_DOWNLOAD = os.getenv("CONTEXT_MODEL_AUTO_DOWNLOAD", "true").strip().lower() in {"1", "true", "yes", "on"}
SOURCE_FALLBACK = os.getenv("CONTEXT_MODEL_SOURCE_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "on"}


def ready(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "model.safetensors").is_file()
        and (path / "tokenizer.json").is_file()
        and (path / "config.json").is_file()
    )


def verify_runtime(path: Path) -> None:
    runtime = StaticModel.from_pretrained(path, normalize=True, force_download=False)
    probe = runtime.encode(["memoria persistente del progetto", "persistent project memory"])
    if probe.shape != (2, TARGET_DIMS) or not np.isfinite(probe).all():
        raise RuntimeError(f"Unexpected runtime probe shape/content: {probe.shape}")


def install_atomically(source: Path) -> None:
    if not ready(source):
        raise RuntimeError(f"Prepared model is incomplete: {source}")
    verify_runtime(source)
    parent = TARGET_PATH.parent
    staged = parent / f".{TARGET_PATH.name}.staged"
    backup = parent / f".{TARGET_PATH.name}.previous"
    shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(source, staged)
    if TARGET_PATH.exists():
        shutil.rmtree(backup, ignore_errors=True)
        os.replace(TARGET_PATH, backup)
    try:
        os.replace(staged, TARGET_PATH)
    except Exception:
        if backup.exists() and not TARGET_PATH.exists():
            os.replace(backup, TARGET_PATH)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def download_compact(work_dir: Path) -> Path:
    if not MODEL_URL:
        raise RuntimeError("CONTEXT_MODEL_URL is empty")
    if len(MODEL_SHA256) != 64 or any(c not in "0123456789abcdef" for c in MODEL_SHA256):
        raise RuntimeError("CONTEXT_MODEL_SHA256 must be a 64-character lowercase hex digest")

    archive = work_dir / "context-model.tar.gz"
    digest = hashlib.sha256()
    request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Postmaster-MCP/9.0"})
    print(f"Downloading compact context model: {MODEL_URL}")
    with urllib.request.urlopen(request, timeout=90) as response, archive.open("wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
    actual = digest.hexdigest()
    if actual != MODEL_SHA256:
        raise RuntimeError(f"Context model SHA256 mismatch: expected {MODEL_SHA256}, got {actual}")

    extract_root = work_dir / "compact-extract"
    extract_root.mkdir()
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            path = PurePosixPath(member.name.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise RuntimeError(f"Unsafe context model archive member: {member.name}")
        tf.extractall(extract_root, filter="data")

    candidates = [extract_root]
    candidates.extend(p for p in extract_root.iterdir() if p.is_dir())
    model_dir = next((p for p in candidates if ready(p)), None)
    if model_dir is None:
        raise RuntimeError("Compact model archive does not contain the expected Model2Vec files")
    return model_dir


def build_from_source(work_dir: Path) -> Path:
    hf_cache = work_dir / "hf-cache"
    output_dir = work_dir / "source-build"
    print(f"Fallback: building {SOURCE_MODEL}@{SOURCE_REVISION} -> {TARGET_DIMS}d int8")
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
    model.save_pretrained(output_dir, model_name="postmaster-static-mrl-multilingual-128d-int8")
    metadata = {
        "source_model": SOURCE_MODEL,
        "source_revision": SOURCE_REVISION,
        "source_license": "Apache-2.0",
        "dims": TARGET_DIMS,
        "quantization": "int8",
        "contains_user_data": False,
        "provisioned_by": "source-fallback",
    }
    (output_dir / "postmaster-model.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return output_dir


def main() -> None:
    if ready(TARGET_PATH):
        print(f"Context model already present: {TARGET_PATH}")
        return
    if TARGET_DIMS != 128:
        raise SystemExit("v9 runtime currently validates only CONTEXT_MODEL_DIMS=128")

    TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=".context-model-prepare-", dir=TARGET_PATH.parent))
    try:
        prepared: Path | None = None
        if AUTO_DOWNLOAD and MODEL_URL:
            try:
                prepared = download_compact(work_dir)
                print("Compact context model download verified by SHA256")
            except Exception as exc:
                if not SOURCE_FALLBACK:
                    raise
                print(f"WARNING: compact model download failed: {type(exc).__name__}: {exc}")

        if prepared is None:
            if not SOURCE_FALLBACK:
                raise RuntimeError("No context model is available and source fallback is disabled")
            prepared = build_from_source(work_dir)

        install_atomically(prepared)
        print(f"Context model ready: {TARGET_PATH}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
