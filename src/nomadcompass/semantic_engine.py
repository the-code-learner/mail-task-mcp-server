from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any


class SemanticError(RuntimeError):
    pass


class SemanticEngine:
    """Lightweight local-only semantic runtime.

    Model provisioning belongs to scripts/prepare_context_model.py. Keeping
    provisioning outside the request path makes search deterministic and lets
    lexical FTS remain available when the local model is unavailable.
    """

    def __init__(self):
        self.enabled = os.getenv("CONTEXT_SEMANTIC_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.model_path = Path(
            os.getenv("CONTEXT_MODEL_PATH", "/data/models/static-similarity-mrl-multilingual-int8-128d")
        )
        self.model_id = (
            os.getenv("CONTEXT_MODEL_ID", "static-similarity-mrl-multilingual-v1-128d-int8").strip()
            or "static-similarity-mrl-multilingual-v1-128d-int8"
        )
        self._model = None
        self._np = None
        self._lock = threading.RLock()
        self.last_error: str | None = None

    @staticmethod
    def _has_model_files(path: Path) -> bool:
        return path.is_dir() and (path / "model.safetensors").is_file() and (path / "tokenizer.json").is_file()

    def ensure_model(self, download_if_missing: bool = False) -> bool:
        del download_if_missing  # retained for compatibility with the v9 context engine
        if not self.enabled:
            self.last_error = "Semantic search disabled by CONTEXT_SEMANTIC_ENABLED"
            return False
        with self._lock:
            if self._model is not None:
                return True
            try:
                if not self._has_model_files(self.model_path):
                    raise SemanticError(f"Model not found at {self.model_path}")
                try:
                    import numpy as np
                    from model2vec import StaticModel
                except Exception as exc:
                    raise SemanticError(f"model2vec runtime unavailable: {exc}") from exc
                self._np = np
                self._model = StaticModel.from_pretrained(str(self.model_path), normalize=True, force_download=False)
                self.last_error = None
                return True
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._model = None
                return False

    def encode(self, texts: list[str]) -> Any:
        if not texts:
            return []
        if not self.ensure_model():
            raise SemanticError(self.last_error or "Semantic model unavailable")
        with self._lock:
            arr = self._model.encode(texts, show_progress_bar=False, use_multiprocessing=False)
            return self._np.asarray(arr, dtype=self._np.float32)

    def vector_bytes(self, vector: Any) -> tuple[bytes, int]:
        if self._np is None:
            import numpy as np
            self._np = np
        arr = self._np.asarray(vector, dtype=self._np.float32).reshape(-1)
        return arr.tobytes(order="C"), int(arr.shape[0])

    def vector_from_bytes(self, blob: bytes, dims: int) -> Any:
        if self._np is None:
            import numpy as np
            self._np = np
        return self._np.frombuffer(blob, dtype=self._np.float32, count=int(dims))

    def cosine(self, query_vector: Any, blob: bytes, dims: int) -> float:
        if self._np is None:
            import numpy as np
            self._np = np
        q = self._np.asarray(query_vector, dtype=self._np.float32).reshape(-1)
        v = self.vector_from_bytes(blob, dims)
        if q.shape[0] != v.shape[0]:
            return -1.0
        qn = float(self._np.linalg.norm(q))
        vn = float(self._np.linalg.norm(v))
        if qn <= 0.0 or vn <= 0.0:
            return -1.0
        return float(self._np.dot(q, v) / (qn * vn))

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self._model is not None,
            "local_model_present": self._has_model_files(self.model_path),
            "model_id": self.model_id,
            "model_path": str(self.model_path),
            "provisioning": "local-bootstrap",
            "last_error": self.last_error,
        }
