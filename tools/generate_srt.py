import os
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import sys
import time
from pathlib import Path
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_millis = int(round(seconds * 1000))
    millis = total_millis % 1000
    total_seconds = total_millis // 1000
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    mins = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

def main():
    audio_path = Path(r"Sources/The Betrayed Wolfless Is The Lycan King.mp3")
    if not audio_path.exists():
        print(f"Error: {audio_path} does not exist!")
        sys.exit(1)
        
    out_srt_sources = audio_path.with_suffix(".srt")
    out_srt_output = Path(r"output") / out_srt_sources.name
    out_srt_output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Audio file: {audio_path} ({audio_path.stat().st_size / (1024*1024):.2f} MB)")
    print(f"Target SRT 1: {out_srt_sources}")
    print(f"Target SRT 2: {out_srt_output}")

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    print(f"Using device: {device}, dtype: {dtype}")

    model_id = "openai/whisper-base"
    print(f"Loading Whisper model ({model_id})...")
    t0 = time.time()
    
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        model_id,
        dtype=dtype,
        low_cpu_mem_usage=True,
        use_safetensors=True
    )
    model.to(device)
    processor = AutoProcessor.from_pretrained(model_id)

    pipe = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        dtype=dtype,
        device=device,
        return_timestamps=True,
    )
    print(f"Model loaded in {time.time() - t0:.2f}s")

    print("Starting transcription of full audio file...")
    t_start = time.time()
    
    with torch.inference_mode():
        result = pipe(
            str(audio_path),
            chunk_length_s=30,
            batch_size=24,
            return_timestamps=True,
            generate_kwargs={"language": "en", "task": "transcribe"}
        )

    t_end = time.time()
    total_time = t_end - t_start
    print(f"Transcription completed in {total_time:.2f}s ({total_time / 60:.2f} minutes)!")

    chunks = result.get("chunks", [])
    print(f"Total chunks / segments produced: {len(chunks)}")

    srt_lines = []
    index = 1
    last_end = 0.0

    for chunk in chunks:
        text = chunk.get("text", "").strip()
        if not text:
            continue
        ts = chunk.get("timestamp", (None, None))
        start_t = ts[0] if ts[0] is not None else last_end
        end_t = ts[1] if ts[1] is not None else (start_t + 2.0)
        
        if end_t <= start_t:
            end_t = start_t + 1.0

        start_str = format_timestamp(start_t)
        end_str = format_timestamp(end_t)
        last_end = end_t

        srt_lines.append(f"{index}\n{start_str} --> {end_str}\n{text}\n")
        index += 1

    srt_content = "\n".join(srt_lines)
    
    out_srt_sources.write_text(srt_content, encoding="utf-8")
    print(f"Saved SRT to {out_srt_sources} ({len(srt_lines)} subtitles)")

    out_srt_output.write_text(srt_content, encoding="utf-8")
    print(f"Saved SRT copy to {out_srt_output}")

    # Preview first 10 subtitles
    print("\n--- First 10 Subtitles Preview ---")
    for srt_entry in srt_lines[:10]:
        print(srt_entry.strip())
        print()

    # Preview last 5 subtitles
    print("--- Last 5 Subtitles Preview ---")
    for srt_entry in srt_lines[-5:]:
        print(srt_entry.strip())
        print()

    print("ALL DONE SUCCESSFULLY!")

if __name__ == "__main__":
    main()
