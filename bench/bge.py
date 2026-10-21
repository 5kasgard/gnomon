"""BAAI/bge-small-en-v1.5 through ONNX Runtime on CPU (no torch): a small
transformer embedder, for comparison with the static embeddings."""
from __future__ import annotations

from pathlib import Path

import numpy as np

DIR = Path("/home/claude/models/bge")
QUERY = "Represent this sentence for searching relevant passages: "


class BGE:
    def __init__(self, path: Path = DIR, max_len: int = 256):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_file(str(path / "tokenizer.json"))
        self.tok.enable_truncation(max_len)
        self.tok.enable_padding()
        self.sess = ort.InferenceSession(str(path / "onnx/model.onnx"), providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}

    def encode(self, texts, query: bool = False, batch: int = 64) -> np.ndarray:
        out = []
        for s in range(0, len(texts), batch):
            enc = self.tok.encode_batch([(QUERY + t) if query else t for t in texts[s:s + batch]])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            h = self.sess.run(None, feed)[0][:, 0]  # CLS
            out.append(h / np.linalg.norm(h, axis=1, keepdims=True))
        return np.vstack(out)
