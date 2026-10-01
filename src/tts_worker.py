"""VoxCPM2 synthesis worker.

Runs under a Python that has torch and VoxCPM2 (ComfyUI's embedded Python by default), apart from
the pipeline's own venv. Reads a job file, writes one WAV per item and prints one JSON line per
result so the pipeline can follow along.

    python tts_worker.py job.json
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf


def emit(**fields):
    print(json.dumps(fields, ensure_ascii=False), flush=True)


def load_model(job):
    # voxcpm_src goes in front so ComfyUI-VoxCPM2's bundled package wins over any pip install;
    # that package imports comfy.*, hence comfy_root on the path too.
    for path in (job.get("comfy_root"), job.get("voxcpm_src")):
        if path:
            sys.path.insert(0, path)
    import torch
    from voxcpm import VoxCPM

    tts = VoxCPM(voxcpm_model_path=job["model_dir"], enable_denoiser=False, optimize=False)
    if torch.cuda.is_available():
        # Loaded weights stay on the CPU until moved; the audio VAE must run in float32.
        tts.tts_model.to("cuda")
        tts.tts_model.audio_vae.to(torch.float32)
    return tts


def trim_silence(wav, sr, floor_db=-40.0, pad_start=0.05, pad_end=0.12):
    frame = sr // 100
    n = len(wav) // frame
    if n == 0:
        return wav
    rms = np.sqrt(np.mean(wav[: n * frame].reshape(n, frame) ** 2, axis=1))
    loud = np.nonzero(rms > rms.max() * 10 ** (floor_db / 20))[0]
    if len(loud) == 0:
        return wav
    a = max(0, loud[0] * frame - int(pad_start * sr))
    b = min(len(wav), (loud[-1] + 1) * frame + int(pad_end * sr))
    return wav[a:b]


def strip_lead_blip(wav, sr, floor_db=-35.0, max_blip=0.3, min_gap=0.3):
    """Drop a short sound cut off from the speech after it, e.g. the "အဲ" continuation cloning leaves.

    Only a first sound of at most ``max_blip`` seconds followed by at least ``min_gap`` of silence
    is removed; gaps under 80 ms count as part of the same sound.
    """
    frame = sr // 100
    n = len(wav) // frame
    if n == 0:
        return wav
    rms = np.sqrt(np.mean(wav[: n * frame].reshape(n, frame) ** 2, axis=1)) + 1e-9
    voiced = np.nonzero(20 * np.log10(rms / np.percentile(rms, 95)) > floor_db)[0]
    if len(voiced) < 2:
        return wav
    k = 0
    while k + 1 < len(voiced) and voiced[k + 1] - voiced[k] <= 8:
        k += 1
    if k + 1 == len(voiced):
        return wav
    blip = (voiced[k] - voiced[0] + 1) / 100
    gap = (voiced[k + 1] - voiced[k] - 1) / 100
    if blip <= max_blip and gap >= min_gap:
        return wav[max(0, voiced[k + 1] * frame - int(0.03 * sr)):]
    return wav


def synth(tts, job, text, ref):
    kwargs = {"text": text, "cfg_value": job["cfg_value"], "inference_timesteps": job["inference_timesteps"]}
    if ref:
        kwargs["reference_wav_path"] = ref
        if job.get("prompt_text"):
            kwargs["prompt_wav_path"] = ref
            kwargs["prompt_text"] = job["prompt_text"]
    sr = tts.tts_model.sample_rate
    return trim_silence(strip_lead_blip(tts.generate(**kwargs), sr), sr)


def main():
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    t0 = time.time()
    tts = load_model(job)
    sr = tts.tts_model.sample_rate
    emit(event="loaded", seconds=round(time.time() - t0, 1), sample_rate=sr)

    ref = job.get("reference_wav")
    if not ref or not Path(ref).exists():
        # No narrator recording: design a voice once and clone it from then on, so every
        # line (and every episode) keeps the same narrator.
        ref = job["auto_voice_wav"]
        if not Path(ref).exists():
            wav = synth(tts, job, f"({job['voice_design']}){job['seed_text']}", None)
            Path(ref).parent.mkdir(parents=True, exist_ok=True)
            sf.write(ref, wav, sr, subtype="FLOAT")
            emit(event="voice_created", path=ref)

    for item in job["items"]:
        out = Path(item["out"])
        try:
            if out.exists():
                emit(id=item["id"], status="cached", duration=sf.info(str(out)).duration)
                continue
            t1 = time.time()
            wav = synth(tts, job, item["text"], ref)
            out.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(out), wav, sr, subtype="FLOAT")
            emit(id=item["id"], status="ok", duration=len(wav) / sr, seconds=round(time.time() - t1, 1))
        except Exception as exc:  # keep going; the pipeline reports failed lines
            emit(id=item["id"], status="error", error=f"{type(exc).__name__}: {exc}")
    emit(event="done", seconds=round(time.time() - t0, 1))


if __name__ == "__main__":
    main()
