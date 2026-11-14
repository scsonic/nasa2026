# Runs INSIDE the Colab kernel. The backend prepends `PARAMS = {...}`.
# Kernel state persists between `colab exec` calls, so the model stays loaded
# in globals() and later generate calls reuse it.
import time

import torch

if globals().get("_MG_ID") != PARAMS["model"]:
    from transformers import AutoProcessor, MusicgenForConditionalGeneration

    _t0 = time.time()
    _MG_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    _MG_PROC = AutoProcessor.from_pretrained(PARAMS["model"])
    _MG_MODEL = MusicgenForConditionalGeneration.from_pretrained(
        PARAMS["model"], torch_dtype=torch.float16 if _MG_DEVICE == "cuda" else torch.float32
    ).to(_MG_DEVICE)
    _MG_MODEL.eval()
    _MG_ID = PARAMS["model"]
    print(f"MG_READY loaded {_MG_ID} on {_MG_DEVICE} in {time.time() - _t0:.1f}s", flush=True)
else:
    print(f"MG_READY cached {_MG_ID}", flush=True)
