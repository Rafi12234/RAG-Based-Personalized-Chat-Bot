"""Actual tokenizer/ONNX Runtime pair-classifier contract tests; synthetic graph."""
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

try:
    import onnx
    import onnxruntime
    import numpy
    from onnx import TensorProto, helper
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from tokenizers.processors import TemplateProcessing
    HAS_RUNTIME=True
except ImportError:
    HAS_RUNTIME=False

from .local_pair_model import LocalPairModel, PairPin
from .pinned_e5 import ModelPinError


@unittest.skipUnless(HAS_RUNTIME,"Optional ONNX test dependencies")
class PairTests(unittest.TestCase):
    def test_real_pair_runtime_and_pin(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            tokenizer=Tokenizer(WordLevel({"[UNK]":0,"[PAD]":1,"[CLS]":2,"[SEP]":3,
                                           "alpha":4,"বাংলা":5},unk_token="[UNK]"))
            tokenizer.pre_tokenizer=Whitespace()
            tokenizer.post_processor=TemplateProcessing(single="[CLS] $A [SEP]",
                  pair="[CLS] $A [SEP] $B:1 [SEP]:1",special_tokens=[("[CLS]",2),("[SEP]",3)])
            tokenizer.enable_truncation(max_length=8)
            tp=root/"tokenizer.json"
            tokenizer.save(str(tp))
            # Constant 3-class logits per batch element, with dynamic batch dimension.
            # ReduceSum attention mask -> [batch,1], multiply by zero then add logits.
            zero=helper.make_tensor("zero",TensorProto.FLOAT,[1],[0.])
            bias=helper.make_tensor("bias",TensorProto.FLOAT,[1,3],[5.,0.,-5.])
            axes=helper.make_tensor("axes",TensorProto.INT64,[1],[1])
            graph=helper.make_graph([
                helper.make_node("Cast",["attention_mask"],["float_mask"],to=TensorProto.FLOAT),
                helper.make_node("ReduceSum",["float_mask","axes"],["sum"],keepdims=1),
                helper.make_node("Mul",["sum","zero"],["zeros"]),
                helper.make_node("Add",["zeros","bias"],["logits"]),
            ],"pair-fixture",[
                helper.make_tensor_value_info("input_ids",TensorProto.INT64,["batch","seq"]),
                helper.make_tensor_value_info("attention_mask",TensorProto.INT64,["batch","seq"]),
            ],[helper.make_tensor_value_info("logits",TensorProto.FLOAT,["batch",3])],
            [zero,bias,axes])
            model=helper.make_model(graph,opset_imports=[helper.make_opsetid("",13)])
            model.ir_version=8
            mp=root/"model.onnx"
            onnx.save(model,str(mp))
            pin=PairPin("fixture","a"*40,sha256(mp.read_bytes()).hexdigest(),
                        sha256(tp.read_bytes()).hexdigest(),("entailment","neutral","contradiction"))
            engine=LocalPairModel(pin,mp,tp)
            rows=engine.score([("alpha","বাংলা"),("বাংলা","alpha alpha")])
            self.assertEqual(len(rows),2)
            self.assertGreater(rows[0]["entailment"],.99)
            self.assertAlmostEqual(sum(rows[0].values()),1.)
            with self.assertRaises(ModelPinError):
                engine.score([("alpha "*520,"বাংলা")])
            tp.write_bytes(tp.read_bytes()+b"x")
            with self.assertRaisesRegex(ModelPinError,"checksum"):
                LocalPairModel(pin,mp,tp)


if __name__=="__main__":
    unittest.main()
