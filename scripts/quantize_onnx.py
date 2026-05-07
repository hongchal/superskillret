"""Quantize exported ONNX model to INT8 (dynamic quantization).

Usage:
    .venv/bin/python scripts/quantize_onnx.py \
        --src onnx_model \
        --dst onnx_model_int8

Produces a quantized ONNX file that is ~4x smaller and 2-3x faster on CPU,
with minimal quality loss for sentence-embedding tasks.
"""

import argparse
import shutil
import time
from pathlib import Path

from onnxruntime.quantization import quantize_dynamic, QuantType


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="source dir from optimum-cli export")
    ap.add_argument("--dst", required=True, help="destination dir for quantized model")
    args = ap.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)

    # Copy everything except model.onnx/model.onnx_data, which we replace with the quantized version
    for item in src.iterdir():
        if item.name in ("model.onnx", "model.onnx_data"):
            continue
        target = dst / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)

    src_model = src / "model.onnx"
    dst_model = dst / "model.onnx"

    print(f"Quantizing {src_model} -> {dst_model} (INT8 dynamic)")
    t0 = time.time()
    quantize_dynamic(
        model_input=str(src_model),
        model_output=str(dst_model),
        weight_type=QuantType.QInt8,
    )
    print(f"Quantization complete in {time.time()-t0:.1f}s")

    # Report sizes
    src_total = sum(f.stat().st_size for f in src.iterdir() if f.name.startswith("model.onnx"))
    dst_total = sum(f.stat().st_size for f in dst.iterdir() if f.name.startswith("model.onnx"))
    print(f"Source total:      {src_total/1e6:.1f} MB")
    print(f"Quantized total:   {dst_total/1e6:.1f} MB  ({dst_total/src_total*100:.1f}% of original)")


if __name__ == "__main__":
    main()
