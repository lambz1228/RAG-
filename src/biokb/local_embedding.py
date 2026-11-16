"""显式启用的本地E5编码器；不联网、不自动下载、不在导入时加载模型。"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from .errors import ProviderError

MODEL = "intfloat/multilingual-e5-small"
MAX_TOKENS = 512
PREFIXES = {"query": "query: ", "document": "passage: "}


def masked_mean_pool(hidden, attention_mask):
    import numpy as np

    values, mask = np.asarray(hidden), np.asarray(attention_mask)
    if values.ndim != 3 or mask.shape != values.shape[:2] or values.shape[2] != 384:
        raise ProviderError("本地向量输出形状不正确")
    counts = mask.sum(axis=1, keepdims=True)
    if np.any(counts == 0) or not np.all(np.isfinite(values)):
        raise ProviderError("本地向量输出无有效词元或含非法数值")
    return ((values * mask[:, :, None]).sum(axis=1) / counts).tolist()


class LocalE5:
    def __init__(self, directory: str, revision: str, threads: int):
        self.directory, self.threads = Path(directory), threads
        try:
            manifest = json.loads(
                (self.directory / "MODEL_MANIFEST.json").read_text(encoding="utf-8")
            )
            if (
                manifest["repository"] != MODEL
                or manifest["revision"] != revision
                or manifest["format"] != "onnx-qint8-avx512-vnni"
            ):
                raise ProviderError("本地向量模型、格式或修订与配置不一致")
            required = {
                "model.onnx",
                "tokenizer.json",
                "config.json",
                "tokenizer_config.json",
                "README.md",
            }
            if set(manifest["files"]) != required:
                raise ProviderError("本地向量模型清单缺少必需文件")
            for name, row in manifest["files"].items():
                digest = hashlib.sha256()
                with (self.directory / name).open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != row["sha256"]:
                    raise ProviderError("本地向量模型文件摘要不一致")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ProviderError("本地向量模型清单不可用，请先下载并核对模型") from exc
        self.manifest = manifest
        identity = {
            "backend": "onnx_e5",
            "model": MODEL,
            "revision": revision,
            "files": manifest["files"],
            "pooling": "attention-masked-mean",
            "normalization": "l2",
            "max_tokens": MAX_TOKENS,
            "prefixes": PREFIXES,
        }
        self.fingerprint = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest()[:24]
        self._lock = threading.RLock()
        self._tokenizer = self._session = None

    def _infer(self, texts):
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise ProviderError("本地向量模型需要安装项目的local-embedding可选依赖") from exc
        with self._lock:
            if self._session is None:
                options = ort.SessionOptions()
                options.intra_op_num_threads = self.threads
                options.inter_op_num_threads = 1
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                self._session = ort.InferenceSession(
                    str(self.directory / "model.onnx"), options, providers=["CPUExecutionProvider"]
                )
                self._tokenizer = Tokenizer.from_file(str(self.directory / "tokenizer.json"))
                self._tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
                self._tokenizer.enable_truncation(max_length=MAX_TOKENS)
            encoded = self._tokenizer.encode_batch(texts)
            ids = np.asarray([row.ids for row in encoded], dtype=np.int64)
            mask = np.asarray([row.attention_mask for row in encoded], dtype=np.int64)
            available = {
                "input_ids": ids,
                "attention_mask": mask,
                "token_type_ids": np.zeros_like(ids),
            }
            feed = {item.name: available[item.name] for item in self._session.get_inputs()}
            hidden = self._session.run(None, feed)[0]
            return masked_mean_pool(hidden, mask)

    def encode(self, texts, role="document"):
        if role not in PREFIXES:
            raise ProviderError("本地向量编码角色不正确")
        output = []
        for start in range(0, len(texts), 16):
            batch = [PREFIXES[role] + text for text in texts[start : start + 16]]
            try:
                rows = self._infer(batch)
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError("本地向量编码失败，请检查模型与运行时") from exc
            if len(rows) != len(batch):
                raise ProviderError("本地向量接口未返回所有文本")
            output.extend(rows)
        return output
