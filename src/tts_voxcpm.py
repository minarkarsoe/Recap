"""Run VoxCPM2 synthesis in its own Python process and collect clip durations."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from .config import resolve

log = logging.getLogger(__name__)

WORKER = Path(__file__).with_name("tts_worker.py")


class TTSError(RuntimeError):
    pass


class VoxCPM2TTS:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.python = Path(cfg["python"])
        if not self.python.exists():
            raise TTSError(f"TTS Python not found: {self.python} (set tts.python in settings.yaml)")
        self.reference = resolve(cfg.get("reference_wav"))
        self.auto_voice = resolve(cfg["auto_voice_wav"])

    @property
    def voice_file(self) -> Path:
        return self.reference if self.reference and self.reference.exists() else self.auto_voice

    def ensure_voice(self, tts_dir: Path) -> None:
        """Make sure a narrator voice file exists before clip names are derived from it."""
        if not self.voice_file.exists():
            log.info("No reference voice at %s; designing a narrator voice once...", self.reference)
            self._run_worker([], tts_dir)

    def voice_key(self) -> str:
        """Fingerprint of everything that changes how a line sounds; part of each clip's name."""
        ref = self.voice_file
        stamp = f"{ref.stat().st_size}:{ref.stat().st_mtime_ns}" if ref.exists() else "none"
        parts = [str(ref), stamp, self.cfg.get("prompt_text", ""), str(self.cfg["cfg_value"]),
                 str(self.cfg["inference_timesteps"])]
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:6]

    def clip_path(self, tts_dir: Path, index: int, text: str) -> Path:
        digest = hashlib.sha1(f"{self.voice_key()}|{text}".encode("utf-8")).hexdigest()[:8]
        return tts_dir / f"beat_{index:04d}_{digest}.wav"

    def synthesize(self, items: list[dict[str, Any]], tts_dir: Path) -> dict[int, float]:
        """Speak every item ({id, text, out}); returns {id: duration}. Existing clips are reused."""
        todo = sum(1 for i in items if not Path(i["out"]).exists())
        log.info("TTS: %d line(s), %d new", len(items), todo)
        return self._run_worker(items, tts_dir, todo)

    def _run_worker(self, items: list[dict[str, Any]], tts_dir: Path, todo: int = 0) -> dict[int, float]:
        tts_dir.mkdir(parents=True, exist_ok=True)
        job = {
            "comfy_root": self.cfg.get("comfy_root"),
            "voxcpm_src": self.cfg.get("voxcpm_src"),
            "model_dir": self.cfg["model_dir"],
            "reference_wav": str(self.reference) if self.reference else None,
            "prompt_text": self.cfg.get("prompt_text") or "",
            "auto_voice_wav": str(self.auto_voice),
            "voice_design": self.cfg["voice_design"],
            "seed_text": self.cfg["seed_text"],
            "cfg_value": self.cfg["cfg_value"],
            "inference_timesteps": self.cfg["inference_timesteps"],
            "items": items,
        }
        job_file = tts_dir / "job.json"
        job_file.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        worker_log = tts_dir / "worker.log"

        # stderr goes to a file: the model's progress bars would otherwise fill the pipe and stall.
        env = dict(os.environ, PYTHONIOENCODING="utf-8", TQDM_DISABLE="1")
        with open(worker_log, "w", encoding="utf-8") as err:
            proc = subprocess.Popen(
                [str(self.python), str(WORKER), str(job_file)],
                stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8", errors="replace", env=env,
            )
            durations: dict[int, float] = {}
            failed: list[str] = []
            new_done = 0
            for line in proc.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                msg = json.loads(line)
                event, status = msg.get("event"), msg.get("status")
                if event == "loaded":
                    log.info("VoxCPM2 loaded in %ss", msg["seconds"])
                elif event == "voice_created":
                    log.warning("Designed a narrator voice and saved it to %s. Put your own recording at "
                                "%s to use that instead.", msg["path"], self.reference)
                elif event == "done":
                    log.info("TTS worker finished in %ss", msg["seconds"])
                elif status in ("ok", "cached"):
                    durations[msg["id"]] = msg["duration"]
                    if status == "ok":
                        new_done += 1
                        if new_done % 10 == 0 or new_done == todo:
                            log.info("TTS: %d/%d new lines", new_done, todo)
                elif status == "error":
                    failed.append(f"beat {msg['id']}: {msg['error']}")
            proc.wait()

        if proc.returncode != 0:
            tail = "\n".join(worker_log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-15:])
            raise TTSError(f"TTS worker crashed (exit {proc.returncode}). See {worker_log}\n{tail}")
        for f in failed:
            log.error("TTS failed for %s", f)
        return durations
