"""tools/export_trocr.py - export TrOCR-small-handwritten to int8 ONNX for the digit reader (T13).

    python -m tools.export_trocr --out models

Offline tool, run once by a developer (locally or on Colab/Kaggle). It needs ``torch`` and
``transformers``, which are deliberately **not** runtime dependencies: the runtime reads the
exported files with ``onnxruntime`` alone (PLAN §3.7, 512 MB budget).

Writes three files into ``--out`` (git-ignored, provenance in ``models/README.md``):

* ``trocr_small_encoder_int8.onnx`` - pixel_values [B,3,H,W] -> encoder_hidden_states
* ``trocr_small_decoder_int8.onnx`` - input_ids [B,T] + encoder_hidden_states -> logits [B,T,V]
* ``trocr_small_vocab.json`` - token strings, special ids and the image preprocessing

The decoder is exported without a key/value cache: a digit field is at most a dozen tokens, so
re-running the decoder over the whole prefix costs little and keeps the graph simple.
Both graphs are int8 dynamic-quantized; the script fails when the pair exceeds the size budget.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

SOURCE_MODEL = "microsoft/trocr-small-handwritten"
ENCODER_FILE = "trocr_small_encoder_int8.onnx"
DECODER_FILE = "trocr_small_decoder_int8.onnx"
VOCAB_FILE = "trocr_small_vocab.json"
# PLAN T13: the whole model must fit beside OpenCV and Tesseract on a 512 MB host.
SIZE_BUDGET_MB = 80.0
OPSET = 17


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _special_id(model: Any, name: str) -> int:
    """A special token id from wherever this transformers version keeps it: the model config,
    the generation config, or the decoder's config. Raises KeyError when none has it."""
    for source in (model.config, model.generation_config, model.config.decoder):
        value = getattr(source, name, None)
        if value is not None:
            return int(value)
    raise KeyError(name)


def export(out: Path, revision: str | None) -> int:
    """Export, quantize and describe the model. Returns a process exit code."""
    from onnxruntime.quantization import QuantType, quantize_dynamic
    import torch
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

    rev = revision or "main"
    processor: Any = TrOCRProcessor.from_pretrained(SOURCE_MODEL, revision=rev)
    model: Any = VisionEncoderDecoderModel.from_pretrained(SOURCE_MODEL, revision=rev)
    model.eval()
    image_proc = processor.image_processor
    size = image_proc.size
    height, width = int(size["height"]), int(size["width"])

    class Encoder(torch.nn.Module):
        def __init__(self, m: Any) -> None:
            super().__init__()
            self.m = m

        def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
            out: torch.Tensor = self.m.encoder(pixel_values=pixel_values).last_hidden_state
            return out

    class Decoder(torch.nn.Module):
        def __init__(self, m: Any) -> None:
            super().__init__()
            self.m = m

        def forward(
            self, input_ids: torch.Tensor, encoder_hidden_states: torch.Tensor
        ) -> torch.Tensor:
            # Going through the full model applies enc_to_dec_proj when the widths differ.
            logits: torch.Tensor = self.m(
                encoder_outputs=(encoder_hidden_states,),
                decoder_input_ids=input_ids,
                use_cache=False,
            ).logits
            return logits

    out.mkdir(parents=True, exist_ok=True)
    pixel = torch.zeros(1, 3, height, width)
    with torch.no_grad():
        hidden = Encoder(model)(pixel)
    ids = torch.full((1, 3), _special_id(model, "decoder_start_token_id"), dtype=torch.long)

    with tempfile.TemporaryDirectory() as tmp:
        enc_fp32, dec_fp32 = Path(tmp) / "encoder.onnx", Path(tmp) / "decoder.onnx"
        torch.onnx.export(
            Encoder(model),
            (pixel,),
            str(enc_fp32),
            input_names=["pixel_values"],
            output_names=["encoder_hidden_states"],
            dynamic_axes={"pixel_values": {0: "batch"}, "encoder_hidden_states": {0: "batch"}},
            opset_version=OPSET,
            dynamo=False,
        )
        torch.onnx.export(
            Decoder(model),
            (ids, hidden),
            str(dec_fp32),
            input_names=["input_ids", "encoder_hidden_states"],
            output_names=["logits"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "tokens"},
                "encoder_hidden_states": {0: "batch"},
                "logits": {0: "batch", 1: "tokens"},
            },
            opset_version=OPSET,
            dynamo=False,
        )
        quantize_dynamic(str(enc_fp32), str(out / ENCODER_FILE), weight_type=QuantType.QUInt8)
        quantize_dynamic(str(dec_fp32), str(out / DECODER_FILE), weight_type=QuantType.QUInt8)

    tokenizer: Any = processor.tokenizer
    vocab = tokenizer.convert_ids_to_tokens(list(range(len(tokenizer))))
    meta = {
        "source_model": SOURCE_MODEL,
        "revision": rev,
        "image_height": height,
        "image_width": width,
        "image_mean": [float(v) for v in image_proc.image_mean],
        "image_std": [float(v) for v in image_proc.image_std],
        "rescale_factor": float(image_proc.rescale_factor),
        "decoder_start_token_id": _special_id(model, "decoder_start_token_id"),
        "eos_token_id": _special_id(model, "eos_token_id"),
        "pad_token_id": _special_id(model, "pad_token_id"),
        "special_ids": sorted(int(i) for i in tokenizer.all_special_ids),
        "tokens": vocab,
    }
    (out / VOCAB_FILE).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    total = 0.0
    for name in (ENCODER_FILE, DECODER_FILE, VOCAB_FILE):
        path = out / name
        megabytes = path.stat().st_size / 1e6
        total += megabytes
        print(f"{name}: {megabytes:.1f} MB sha256={_sha256(path)}")
    print(f"total: {total:.1f} MB (budget {SIZE_BUDGET_MB:.0f} MB)")
    if total >= SIZE_BUDGET_MB:
        print("ERROR: over the size budget", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Export TrOCR-small to int8 ONNX.")
    parser.add_argument("--out", type=Path, default=Path("models"), help="output directory")
    parser.add_argument("--revision", default=None, help="Hugging Face revision (commit hash)")
    args = parser.parse_args(argv)
    return export(args.out, args.revision)


if __name__ == "__main__":
    sys.exit(main())
