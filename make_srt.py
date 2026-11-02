#!/usr/bin/env python3
"""Subtitles with timecodes from any audio/video file (faster-whisper, word timestamps).

    .venv\\Scripts\\python.exe make_srt.py "E:\\file.mp3" [--model small] [--lang ru]

Creates projects\\<name>\\ with <name>.srt, <name>.txt (plain text with timecodes) and
<name>.lowconf.tsv (words Whisper was unsure about: candidates for the user to check by ear).
"""
import argparse
import re
import sys
from pathlib import Path

import voice_clean as v

END_PUNCT = (".", "!", "?", "…")
MAX_CHARS_LINE = 42
MAX_CUE_CHARS = 84
MAX_CUE_SEC = 5.5


def ts(t):
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_two_lines(text):
    if len(text) <= MAX_CHARS_LINE:
        return text
    mid = len(text) // 2
    best = None
    for m in re.finditer(r" ", text):
        d = abs(m.start() - mid)
        if best is None or d < best[0]:
            best = (d, m.start())
    return text if best is None else text[:best[1]] + "\n" + text[best[1] + 1:]


def build_cues(words):
    cues, cur = [], []
    for w in words:
        if cur:
            text = " ".join(x["word"] for x in cur)
            dur = cur[-1]["end"] - cur[0]["start"]
            gap = w["start"] - cur[-1]["end"]
            prev = cur[-1]["word"]
            if (dur > MAX_CUE_SEC or len(text) + 1 + len(w["word"]) > MAX_CUE_CHARS or gap > 0.7
                    or (prev.endswith(END_PUNCT) and (dur > 1.5 or len(text) > 40))):
                cues.append(cur)
                cur = []
        cur.append(w)
    if cur:
        cues.append(cur)
    return cues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--prompt", default=None, help="optional initial prompt with names/terms")
    ap.add_argument("--out-root", default=str(Path(__file__).parent / "projects"))
    ap.add_argument("--low", type=float, default=0.45, help="word probability below this is listed as unsure")
    a = ap.parse_args()

    src = Path(a.input)
    stem = re.sub(r'[<>:"/\\|?*]', "_", src.stem)
    folder = Path(a.out_root) / stem
    folder.mkdir(parents=True, exist_ok=True)

    stream = v.pick_audio_stream(src)
    audio = v.decode(src, 16000, 1, stream)[:, 0]
    print(f"Duration {len(audio)/16000:.1f} s, transcribing ({a.model})...", flush=True)
    from faster_whisper import WhisperModel

    def go(device, compute):
        m = WhisperModel(a.model, device=device, compute_type=compute)
        segs, _ = m.transcribe(audio, language=a.lang, word_timestamps=True, vad_filter=True,
                               condition_on_previous_text=False, initial_prompt=a.prompt)
        return list(segs)

    try:
        segs = go("auto", "auto")
    except RuntimeError:
        segs = go("cpu", "int8")

    words = [{"word": w.word.strip(), "start": w.start, "end": w.end, "p": w.probability}
             for s in segs for w in (s.words or []) if w.word.strip()]
    cues = build_cues(words)

    lines, txt = [], []
    for i, c in enumerate(cues, 1):
        start, end = c[0]["start"], c[-1]["end"]
        if i < len(cues):
            end = min(max(end, start + 0.8), cues[i][0]["start"] - 0.02) if cues[i][0]["start"] - 0.02 > start else end
        text = " ".join(x["word"] for x in c)
        lines.append(f"{i}\n{ts(start)} --> {ts(end)}\n{split_two_lines(text)}\n")
        txt.append(f"[{ts(start)[:8]}] {text}")
    (folder / f"{stem}.srt").write_text("\n".join(lines), encoding="utf-8")
    (folder / f"{stem}.txt").write_text("\n".join(txt) + "\n", encoding="utf-8")

    low = [w for w in words if w["p"] < a.low]
    with open(folder / f"{stem}.lowconf.tsv", "w", encoding="utf-8") as f:
        f.write("time\tword\tprobability\n")
        for w in low:
            f.write(f"{ts(w['start'])}\t{w['word']}\t{w['p']:.2f}\n")
    print(f"Cues: {len(cues)}, words: {len(words)}, unsure words: {len(low)}")
    print(f"Folder: {folder}")


if __name__ == "__main__":
    main()
