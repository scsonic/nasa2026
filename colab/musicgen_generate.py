# Runs INSIDE the Colab kernel after musicgen_setup.py. The backend prepends `PARAMS = {...}`.
import os
import time

import numpy as np
import scipy.io.wavfile as wavfile
import torch

_t0 = time.time()
os.makedirs(os.path.dirname(PARAMS["out"]), exist_ok=True)
_inputs = _MG_PROC(text=[PARAMS["prompt"]], padding=True, return_tensors="pt").to(_MG_DEVICE)
_frame_rate = getattr(_MG_MODEL.config.audio_encoder, "frame_rate", 50)
_tokens = int(PARAMS["seconds"] * _frame_rate)
with torch.inference_mode():
    _audio = _MG_MODEL.generate(**_inputs, do_sample=True, guidance_scale=3.0, max_new_tokens=_tokens)
_sr = _MG_MODEL.config.audio_encoder.sampling_rate
_wave = _audio[0].float().cpu().numpy()  # (channels, samples)
_wave = _wave[0] if _wave.shape[0] == 1 else _wave.T
_peak = float(np.max(np.abs(_wave))) or 1.0
_wave = np.clip(_wave / max(_peak, 1e-6) * 0.95, -1.0, 1.0)
# Short fades so the clip does not click at the start/end.
_fade = int(_sr * 0.05)
_ramp = np.linspace(0.0, 1.0, _fade)
if _wave.ndim == 1:
    _wave[:_fade] *= _ramp
    _wave[-_fade:] *= _ramp[::-1]
else:
    _wave[:_fade] *= _ramp[:, None]
    _wave[-_fade:] *= _ramp[::-1][:, None]
wavfile.write(PARAMS["out"], _sr, (_wave * 32767).astype(np.int16))
print(f"MG_DONE {PARAMS['out']} {len(_wave) / _sr:.1f}s audio in {time.time() - _t0:.1f}s", flush=True)
