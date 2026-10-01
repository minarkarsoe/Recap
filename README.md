# Donghua Burmese Voice-over

YouTube က Donghua video (Chinese subtitle ပါတာ) ကို ယူပြီး မြန်မာလို storytelling ပုံစံ voice-over ထည့်ပေးတဲ့ pipeline ပါ။ အကုန်လုံး ကိုယ့်စက်ထဲမှာပဲ run ပါတယ်။ မူရင်း video ၂၀ မိနစ်ဆိုရင် ၂၀ မိနစ်စာ (speed 1.05x နဲ့ဆို ၁၉ မိနစ်ခန့်) narration ပါတဲ့ video ထွက်လာပါမယ်။

```
YouTube URL ─► yt-dlp (video + zh subtitles)
            ─► subtitle ကို beat တွေ စုတယ် (5–12s)
            ─► Ollama LLM: ဇာတ်လမ်းအကျဉ်း + နာမည် glossary → beat တစ်ခုစီအတွက် Burmese narration
            ─► VoxCPM2 (ComfyUI Python ထဲမှာ run): narration တစ်ကြောင်းချင်းစီ အသံထုတ်
            ─► timeline: narration ကို သူ့ beat အချိန်မှာ နေရာချ၊ ရှည်နေရင် LLM က ချုံ့ / အနည်းငယ် speed တင်
            ─► mix: မူရင်းအသံကို narration အောက်မှာ ducking, BGM, -14 LUFS
            ─► FFmpeg (NVENC): speed, logo, 16:9 / 9:16
```

## လိုအပ်တာတွေ

- **FFmpeg** (PATH ထဲမှာ): `winget install Gyan.FFmpeg`
- **Ollama** နဲ့ model တစ်ခု (`config/settings.yaml` ထဲက `llm.model`)
- **ComfyUI portable + ComfyUI-VoxCPM2** node နဲ့ VoxCPM2 weights။ Pipeline က ComfyUI ရဲ့ embedded Python ကို TTS အတွက်ပဲ ယူသုံးပြီး ComfyUI ထဲကို ဘာမှ ထပ်မသွင်းပါဘူး။ ComfyUI ကိုလည်း ဖွင့်ထားစရာ မလိုပါဘူး။
- GPU: RTX 5070 Ti 16GB နဲ့ စမ်းထားပါတယ် (VoxCPM2 က VRAM ~5.5GB သုံးတယ်)။ LLM နဲ့ TTS ကို တစ်ပြိုင်နက် မ run ပါဘူး။ LLM ပြီးတာနဲ့ Ollama ကနေ unload လုပ်ပေးပါတယ်။

## Setup

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` ထဲမှာ `deno` ပါပါတယ်။ yt-dlp က YouTube အတွက် JavaScript runtime လိုအပ်တာကြောင့်ပါ။

`config/settings.yaml` ထဲက `tts.python`, `tts.comfy_root`, `tts.voxcpm_src`, `tts.model_dir` တို့ကို ComfyUI ရှိတဲ့နေရာနဲ့ ကိုက်အောင် ပြင်ပါ။

## Assets

| ဖိုင် | အသုံး |
|---|---|
| `assets/reference_voices/ref_voice.wav` | Narrator အသံ (5–15 စက္ကန့်၊ နောက်ခံဆူညံသံ မပါတဲ့ စကားပြောသံ)။ `tts.prompt_text` ထဲမှာ အဲ့ဒီအသံထဲက စာသားအတိအကျ ထည့်ထားရင် အသံ ပိုတူပါတယ်။ |
| `assets/logos/logo.png` | Channel logo (နောက်ခံ ကြည်လင်တဲ့ PNG)။ မရှိရင် logo မပါဘဲ render လုပ်ပါတယ်။ |
| `assets/bgm/bgm.mp3` | နောက်ခံတေးဂီတ (optional၊ loop လုပ်ပါတယ်)။ |

`ref_voice.wav` မရှိရင် `tts.voice_design` ကို သုံးပြီး narrator အသံတစ်ခု တစ်ခါတည်း ဖန်တီးပြီး `auto_narrator.wav` အဖြစ် သိမ်းထားပါတယ်။ ဒါကြောင့် episode တိုင်းမှာ အသံတစ်မျိုးတည်း ထွက်ပါတယ်။

## သုံးနည်း

```bash
# အကုန်တစ်ခါတည်း
.venv\Scripts\python.exe main.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --ratio 16:9

# Script ထွက်တာနဲ့ ရပ်ပြီး ကိုယ်တိုင် ဖတ်၊ ပြင် (အကြံပြုပါတယ်)
.venv\Scripts\python.exe main.py --url "..." --stop-after script
#   → work\VIDEO_ID\script.json ထဲက "narration" တွေကို ပြင်ပါ
.venv\Scripts\python.exe main.py --job VIDEO_ID

# ကိုယ့်မှာရှိပြီးသား video + subtitle
.venv\Scripts\python.exe main.py --video ep01.mp4 --subs ep01.srt --ratio both

# Subtitle မပါတဲ့ YouTube video: ကိုယ့် .srt ကို ထည့်ပေးပါ
.venv\Scripts\python.exe main.py --job VIDEO_ID --subs ep01.zh.srt
```

Output: video တစ်ခုစီက ကိုယ်ပိုင် folder ရပါတယ် — `output\<title>\<title> [16x9].mp4`, `output\<title>\<title> [9x16].mp4` နဲ့ YouTube မှာ upload တင်လို့ရတဲ့ `output\<title>\<title>.my.srt` (Burmese narration subtitle)။ အပိုင်းခွဲထားရင် အပိုင်းဖိုင်တွေလည် အဲဒီ folder ထဲမှာ အတူတူ ရှိပါတယ်။

### ရှည်တဲ့ video (အပိုင်းခွဲ)

၂၀ မိနစ်ထက် ရှည်တဲ့ video တွေကို `pipeline.part_minutes` (default ၂၀ မိနစ်) အရှည်ရှိတဲ့ အပိုင်းတွေ ခွဲပြီး လုပ်ပါတယ်။ ဖြတ်တဲ့နေရာကို စကားပြောတွေကြားက အကျယ်ဆုံး အငြိမ်အချိန်မှာ ရွေးပါတယ်။ အပိုင်းတစ်ခုစီအတွက် script → TTS → render ကို အစဉ်လိုက် လုပ်ပြီး ပြီးတဲ့အပိုင်းကို `output\<title>\part01 [16x9].mp4` အဖြစ် ချက်ချင်း ကြည့်လို့ရပါတယ်။ အပိုင်းအားလုံး ပြီးသွားရင် ပြန်ဆက်ပြီး video တစ်ခုတည်း ထုတ်ပေးပါတယ်။ Video ကို re-encode မလုပ်ဘဲ ဆက်ပြီး အသံကိုလည်း တစ်ဆက်တည်း loudnorm လုပ်လို့ ဆက်ထားတဲ့နေရာမှာ ပြတ်တောက်တာ မရှိပါဘူး။

```bash
# ပထမပိုင်းကိုပဲ အရင်စမ်း
.venv\Scripts\python.exe main.py --job VIDEO_ID --parts 1
# ကျန်တာအကုန်ဆက်လုပ်ပြီး ပြန်ဆက်
.venv\Scripts\python.exe main.py --job VIDEO_ID
```

Script တွေကို `work\<id>\parts\p01\script.json`၊ `p02\script.json`... ဆိုပြီး အပိုင်းအလိုက် သိမ်းထားပါတယ်။

### Stage တွေနဲ့ ဆက် run ခြင်း

Stage တစ်ခုချင်းစီရဲ့ ရလဒ်ကို `work\<id>\` ထဲမှာ သိမ်းထားလို့ ရပ်သွားရင် `--job <id>` နဲ့ ရပ်ခဲ့တဲ့နေရာကနေ ဆက် run လို့ရပါတယ်။

| Stage | ထွက်လာတဲ့ဖိုင် | လုပ်တာ |
|---|---|---|
| parse | `segments.json`, `beats.json` | subtitle ဖတ်ပြီး beat တွေ စုတယ် |
| context | `context.json` | ဇာတ်လမ်းအကျဉ်း + နာမည် glossary |
| script | `parts\pNN\script.json` | Beat တစ်ခုစီအတွက် Burmese narration |
| tts | `tts\*.wav`, `parts\pNN\timeline.json` | အသံထုတ်ပြီး timeline ပေါ်မှာ နေရာချတယ် |
| compose | `parts\pNN\mix.wav`, `video_16x9.mp4`, `output\*.mp4` | Audio mix လုပ်ပြီး render၊ ပြီးရင် အပိုင်းတွေကို ပြန်ဆက်တယ် |

- `script.json` ကို ပြင်ပြီး `--job` နဲ့ ပြန် run ရင် ပြောင်းထားတဲ့ အပိုင်းကိုပဲ ပြန်လုပ်ပြီး ပြောင်းထားတဲ့ စာကြောင်းတွေကိုပဲ အသံပြန်ထုတ်ပါတယ်။
- `--from-stage context` ဆိုရင် context stage နဲ့ သူ့နောက်က stage အားလုံးကို အစကနေ ပြန်လုပ်ပါတယ် (ဥပမာ `glossary.yaml` ပြင်ပြီးရင်)။
- `--model gemma4:12b` ဆိုရင် model တခြားတစ်ခုနဲ့ စမ်းကြည့်လို့ရပါတယ်။

## Settings အဓိကများ (`config/settings.yaml`)

- `llm.model`: Burmese ရေးမယ့် model။ Model တစ်ခုနဲ့တစ်ခု အရည်အသွေး အများကြီး ကွာပါတယ်။ `--stop-after script --model ...` နဲ့ နှိုင်းယှဉ်ပြီးမှ ရွေးပါ။
- `config/glossary.yaml`: ဇာတ်ကောင်နာမည်၊ ဂိုဏ်းနာမည်တွေကို episode တိုင်းမှာ စာလုံးပေါင်း တစ်မျိုးတည်း ဖြစ်အောင် ဒီမှာ သတ်မှတ်ပါ (`萧炎: ရှောင်ယန်`)။
- `tts.syllables_per_second`: Narrator ရဲ့ စကားပြောနှုန်း (VoxCPM2 မှာ 4.5)။ LLM ကို စာလုံးရေ ဘယ်လောက်အထိ ရေးခွင့်ပေးမလဲ ဒီနှုန်းနဲ့ တွက်ပါတယ်။
- `tts.max_tempo`: Narration ကို အများဆုံး ဘယ်လောက်အထိ speed တင်မလဲ။ ဒီထက်ပိုရှည်တဲ့ စာကြောင်းတွေကို LLM က ချုံ့ပေးပါတယ်။
- `audio.original_volume` / `duck_volume`: မူရင်းအသံ (BGM, sound effect, Chinese စကားပြော) ရဲ့ volume။ Narration မရှိတဲ့အချိန်နဲ့ narration အောက်မှာ သတ်မှတ်ပါ။
- `video.encoder`: `h264_nvenc` (GPU, မြန်) သို့မဟုတ် `libx264`။

## ပြဿနာဖြေရှင်းခြင်း

- **`yt-dlp failed ... JavaScript`**: venv ထဲမှာ `deno` သွင်းထားမှ ရပါမယ် (`pip install deno`)။
- **`No subtitles found`**: Video မှာ subtitle ကို ပုံထဲမှာ ရိုက်ထည့်ထားတာ (hardsub) ဖြစ်နိုင်ပါတယ်။ `.srt` ကို `--subs` နဲ့ ထည့်ပေးပါ။
- **TTS worker crashed**: `work\<id>\tts\worker.log` ကို ကြည့်ပါ။
- **Burmese မကောင်းရင်**: `script.json` ကို ကိုယ်တိုင်ပြင်ပါ၊ ဒါမှမဟုတ် တခြား model နဲ့ `--from-stage context --model ...` စမ်းကြည့်ပါ။
