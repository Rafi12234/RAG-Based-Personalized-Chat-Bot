"""Phase 4.5/4.6: checksum-pinned local multilingual ONNX pair classifiers.

Supports cross-encoder scalar relevance logits and 3-class NLI logits.
Operator chooses tested multilingual model artifacts and explicit label order.
No download, fallback, remote call, hidden truncation or model tool access.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from .pinned_e5 import _checked_bytes, MAX_MODEL_BYTES, MAX_TOKENIZER_BYTES, ModelPinError


@dataclass(frozen=True)
class PairPin:
    model_id: str
    revision: str
    model_sha256: str
    tokenizer_sha256: str
    labels: tuple[str, ...]
    output_name: str = "logits"

    def __post_init__(self):
        if (not isinstance(self.model_id, str) or not re.fullmatch(r"[\w./-]{1,200}", self.model_id)
                or not isinstance(self.revision, str) or not re.fullmatch(r"[a-f0-9]{40}", self.revision)
                or any(not isinstance(x, str) or not re.fullmatch(r"[a-f0-9]{64}", x)
                       for x in (self.model_sha256, self.tokenizer_sha256))
                or type(self.labels) is not tuple or
                not (self.labels == ("relevance",) or
                     (len(self.labels) == 3 and set(self.labels) == {"entailment","neutral","contradiction"}))
                or not isinstance(self.output_name, str) or not re.fullmatch(r"[\w./:-]{1,128}", self.output_name)):
            raise ModelPinError("Invalid pair model pin")

    @property
    def digest(self):
        return sha256(json.dumps([self.model_id,self.revision,self.model_sha256,
                                 self.tokenizer_sha256,self.labels,self.output_name,
                                 "cpu-pair-int64-v1"], ensure_ascii=True,
                                 separators=(",",":")).encode()).hexdigest()


class LocalPairModel:
    def __init__(self, pin: PairPin, model_path: Path, tokenizer_path: Path):
        if type(pin) is not PairPin:
            raise ModelPinError("Trusted pair-model pin required")
        model = _checked_bytes(Path(model_path), pin.model_sha256, MAX_MODEL_BYTES)
        tok = _checked_bytes(Path(tokenizer_path), pin.tokenizer_sha256, MAX_TOKENIZER_BYTES)
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            self.tok = Tokenizer.from_str(tok.decode("utf-8"))
            self.tok.no_padding()
            self.tok.no_truncation()
            self.pad = self.tok.token_to_id("<pad>")
            if self.pad is None:
                self.pad = self.tok.token_to_id("[PAD]")
            if type(self.pad) is not int or self.pad < 0:
                raise ModelPinError("Pair tokenizer has no pad token")
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = opts.inter_op_num_threads = 1
            opts.log_severity_level = 3
            self.session = ort.InferenceSession(model, sess_options=opts,
                                                providers=["CPUExecutionProvider"])
            ins = self.session.get_inputs()
            self.inputs = {x.name for x in ins}
            needed = {"input_ids","attention_mask"}
            outs = [x for x in self.session.get_outputs() if x.name == pin.output_name]
            if (self.session.get_providers() != ["CPUExecutionProvider"] or
                    self.inputs not in (needed, needed|{"token_type_ids"}) or
                    any(x.type != "tensor(int64)" or len(x.shape)!=2 for x in ins) or
                    len(outs)!=1 or outs[0].type!="tensor(float)" or
                    len(outs[0].shape)!=2 or outs[0].shape[1]!=len(pin.labels)):
                raise ModelPinError("Incompatible pair classifier contract")
        except ModelPinError:
            raise
        except Exception:
            raise ModelPinError("Cannot load pinned pair classifier") from None
        self.pin = pin

    def score(self, pairs):
        import numpy as np
        if not isinstance(pairs,(tuple,list)) or not 1 <= len(pairs) <= 16:
            raise ModelPinError("Pair batch outside bound")
        enc = []
        for pair in pairs:
            if (not isinstance(pair,(tuple,list)) or len(pair)!=2 or
                    any(not isinstance(t,str) or not t.strip() or len(t)>4000 for t in pair)):
                raise ModelPinError("Invalid pair")
            e = self.tok.encode(pair[0],pair[1],add_special_tokens=True)
            if not 1 <= len(e.ids) <= 512 or any(v!=1 for v in e.attention_mask):
                raise ModelPinError("Pair token budget exceeded")
            enc.append(e)
        shape = (len(enc),max(len(e.ids) for e in enc))
        ids = np.full(shape,self.pad,dtype=np.int64)
        mask = np.zeros(shape,dtype=np.int64)
        types = np.zeros(shape,dtype=np.int64)
        for i,e in enumerate(enc):
            n=len(e.ids)
            ids[i,:n],mask[i,:n],types[i,:n]=e.ids,1,e.type_ids
        feeds={"input_ids":ids,"attention_mask":mask}
        if "token_type_ids" in self.inputs:
            feeds["token_type_ids"]=types
        try:
            result=self.session.run([self.pin.output_name],feeds)[0]
            if result.shape!=(len(enc),len(self.pin.labels)) or not np.isfinite(result).all():
                raise ModelPinError("Invalid pair classifier output")
            if len(self.pin.labels)==1:
                return tuple(float(x[0]) for x in result)
            values=result.astype(np.float64)
            probs=np.exp(values-values.max(axis=1,keepdims=True))
            probs/=probs.sum(axis=1,keepdims=True)
            return tuple(dict(zip(self.pin.labels,map(float,row))) for row in probs)
        except ModelPinError:
            raise
        except Exception:
            raise ModelPinError("Pair classifier inference failed") from None
