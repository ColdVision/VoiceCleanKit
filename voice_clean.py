#!/usr/bin/env python3
"""Voice-over cleanup: cut pauses, breaths and clicks, light enhancement, loudness normalization.

How it works
  1. faster-whisper finds the exact start/end of every spoken word (works offline).
  2. Only the words (plus a few ms of padding) are kept; everything between words
     (breaths, mouth clicks, long pauses) is dropped and replaced by a short pause
     of quiet room tone, so cuts never sound "dead".
  3. ffmpeg: declick, high-pass, optional denoise, gentle compression,
     two-pass loudnorm to the target LUFS.

Usage:
    .venv\\Scripts\\python.exe voice_clean.py input.wav -o output.wav
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

SR = 48000
FADE = 0.008  # seconds, fade on every cut edge to avoid clicks


def run(cmd):
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        sys.exit(f"ffmpeg failed:\n{p.stderr.decode(errors='replace')[-1500:]}")
    return p


def decode(path, sr, ch=1, stream=0):
    """Returns float32 array shaped (samples, ch). `stream` = index among the audio streams."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:a:{stream}", "-ac", str(ch),
           "-ar", str(sr), "-f", "f32le", "-"]
    return np.frombuffer(run(cmd).stdout, dtype=np.float32).reshape(-1, ch).copy()


def channel_count(path, stream=0):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"a:{stream}", "-show_entries",
                        "stream=channels", "-of", "csv=p=0", str(path)], capture_output=True)
    try:
        return max(1, min(2, int(p.stdout.decode().strip())))
    except ValueError:
        return 1


def pick_audio_stream(path):
    """Index of the loudest audio stream (screen recorders often put silence on track 1)."""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=index", "-of", "csv=p=0", str(path)], capture_output=True)
    n = len([x for x in p.stdout.decode().split() if x.strip()])
    best, best_v = 0, -1e9
    for i in range(n):
        q = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", f"0:a:{i}",
                            "-af", "volumedetect", "-f", "null", "-"], capture_output=True)
        m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf)", q.stderr.decode(errors="replace"))
        vol = float(m.group(1)) if m and m.group(1) != "-inf" else -1e9
        if vol > best_v:
            best, best_v = i, vol
    return best


def render_cut_video(src, vinfo, audio_wav, segs, pause, out_mp4):
    """Cuts the picture at the same points as the audio and muxes the cleaned wav."""
    parts, labels, t0 = [], [], 0.0
    for i, (a, b) in enumerate(segs):
        ln = (b - a) + (pause if i < len(segs) - 1 else 0.0)
        parts.append(f"[0:v]trim=start={a:.4f}:end={a + ln:.4f},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
    parts.append("".join(labels) + f"concat=n={len(segs)}:v=1:a=0[vout]")
    script = Path(out_mp4).with_suffix(".filter.txt")
    script.write_text(";\n".join(parts), encoding="utf-8")
    enc = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"]
    for codec in (enc, ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]):
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-i", str(audio_wav),
               "-/filter_complex", str(script), "-map", "[vout]", "-map", "1:a",
               *codec, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
               "-movflags", "+faststart", "-shortest", str(out_mp4)]
        if subprocess.run(cmd, capture_output=True).returncode == 0:
            break
    else:
        sys.exit("video render failed")
    script.unlink()


def transcribe_words(audio16k, model_name, lang):
    from faster_whisper import WhisperModel
    def go(device, compute):
        model = WhisperModel(model_name, device=device, compute_type=compute)
        segs, info = model.transcribe(audio16k, language=lang, word_timestamps=True,
                                      vad_filter=False, condition_on_previous_text=False)
        return [(w.start, w.end, w.word.strip()) for s in segs for w in (s.words or [])], info

    try:
        return go("auto", "auto")
    except RuntimeError as e:  # e.g. CUDA libraries missing -> fall back to CPU
        print(f"GPU unavailable ({e}); using CPU.")
        return go("cpu", "int8")


def vad_segments(audio16k, threshold, min_silence_ms):
    """Speech regions via the Silero VAD bundled with faster-whisper (never drops speech
    the way a transcript-based cut can when Whisper skips a passage)."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    opts = VadOptions(threshold=threshold, min_speech_duration_ms=100,
                      min_silence_duration_ms=min_silence_ms, speech_pad_ms=30)
    ts = get_speech_timestamps(audio16k, opts)
    return [(t["start"] / 16000, t["end"] / 16000, "speech") for t in ts]


def _frame_feats(x, sr=16000, win=0.03, hop=0.01):
    """Per-frame level (dB), spectral flatness (100-6000 Hz) and periodicity (70-400 Hz)."""
    n, h = int(win * sr), int(hop * sr)
    k = (len(x) - n) // h
    if k < 1:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    fr = np.stack([x[i * h:i * h + n] * np.hanning(n) for i in range(k)])
    lvl = 20 * np.log10(np.sqrt((fr ** 2).mean(1)) + 1e-9)
    P = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    P = P[:, (f >= 100) & (f < 6000)] + 1e-12
    flat = np.exp(np.log(P).mean(1)) / P.mean(1)
    lo, hi = int(sr / 400), int(sr / 70)
    per = np.zeros(k)
    for i in range(k):
        z = fr[i] - fr[i].mean()
        ac = np.correlate(z, z, "full")[n - 1:]
        per[i] = ac[lo:hi].max() / ac[0] if ac[0] > 1e-12 else 0
    return lvl, flat, per


def trim_breaths(audio16k, regions, rel_db=-15.0, min_trim=0.08):
    """Cuts breath-like tails (quiet, noisy, unvoiced) off the end of speech regions and drops
    regions that consist of such sound only. Loud fricatives at word ends are kept because a
    breath is far quieter than speech. Returns (new_regions, trimmed_seconds, dropped_regions)."""
    lvl_all, _, _ = _frame_feats(audio16k)
    speech_lvl = np.median(np.concatenate([
        _frame_feats(audio16k[int(s * 16000):int(e * 16000)])[0] for s, e, _ in regions[:200]]))
    out, trimmed, dropped = [], 0.0, 0
    for s, e, t in regions:
        seg = audio16k[int(s * 16000):int(e * 16000)]
        lvl, flat, per = _frame_feats(seg)
        if len(lvl) == 0:
            out.append((s, e, t))
            continue
        breathy = (lvl < speech_lvl + rel_db) & ((flat > 0.12) | (per < 0.3))
        if breathy.mean() > 0.85 and (e - s) < 0.8:
            dropped += 1
            trimmed += e - s
            continue
        n = 0
        while n < len(breathy) and breathy[len(breathy) - 1 - n]:
            n += 1
        cut = n * 0.01
        if cut >= min_trim:
            new_e = e - cut + 0.03
            trimmed += e - new_e
            e = new_e
        out.append((s, e, t))
    return out, trimmed, dropped


def room_tone(audio, words, length):
    """Quietest 0.25 s stretch between words; tiled to the requested length."""
    n = int(0.25 * SR)
    best, best_rms = None, 1e9
    prev_end = 0.0
    for start, end, _ in words + [(len(audio) / SR, len(audio) / SR, "")]:
        a, b = int(prev_end * SR), int(start * SR)
        for pos in range(a, b - n, n // 2):
            r = float(np.sqrt(np.mean(audio[pos:pos + n] ** 2)))
            if r < best_rms:
                best, best_rms = pos, r
        prev_end = end
    if best is None:
        return np.zeros(length, dtype=np.float32)
    tone = audio[best:best + n].copy()
    tone *= np.hanning(len(tone)).astype(np.float32) ** 0.25
    reps = int(np.ceil(length / len(tone)))
    return np.tile(tone, reps)[:length].astype(np.float32)


def fade(x, n):
    n = min(n, len(x) // 2)
    if n > 0:
        ramp = np.linspace(0, 1, n, dtype=np.float32)
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


def fade_edges(x, n_in, n_out):
    """Short linear fade-in (click guard) and a smooth half-cosine fade-out into the pause."""
    n_in = min(n_in, len(x) // 2)
    n_out = min(n_out, int(len(x) * 0.4))
    if n_in > 0:
        x[:n_in] *= np.linspace(0, 1, n_in, dtype=np.float32)
    if n_out > 0:
        x[-n_out:] *= (0.5 * (1 + np.cos(np.linspace(0, np.pi, n_out)))).astype(np.float32)
    return x


def assemble(audio, words, pause, pad, max_gap_keep, report, fade_out=0.12):
    dur = len(audio) / SR
    out, cut_log, segs = [], [], []
    if fade_out > 0:
        # smooth fade-out at the end of every phrase (also where the natural gap is kept, because the
        # source often stops abruptly), then silence the remnants of the tail up to the pad
        audio = audio.copy()
        for k, (s, e, _) in enumerate(words):
            nxt = words[k + 1][0] if k + 1 < len(words) else dur
            if nxt - e < 0.08:
                continue
            n = min(int(fade_out * SR), int(0.4 * (e - s) * SR))
            i1 = int(e * SR)
            if n > 0:
                audio[i1 - n:i1] *= (0.5 * (1 + np.cos(np.linspace(0, np.pi, n)))).astype(np.float32)
            audio[i1:int(min(e + pad, nxt - 0.01) * SR)] = 0.0
    # merge words
    # merge words into phrases: gap <= max_gap_keep stays untouched (natural speech rhythm)
    phrases = []
    for s, e, t in words:
        # gaps not longer than the replacement pause are left alone (cutting would only lengthen them)
        if phrases and s - phrases[-1][1] <= max(max_gap_keep, pause):
            phrases[-1][1] = e
            phrases[-1][2].append(t)
        else:
            phrases.append([s, e, [t]])
    tone_len = int(pause * SR)
    tone = room_tone(audio, words, max(tone_len, 1))
    for i, (s, e, t) in enumerate(phrases):
        a = max(0.0, s - pad)
        b = min(dur, e + pad)
        if i > 0:
            ps, pe, _ = phrases[i - 1]
            gap = s - pe
            cut_log.append((pe, s, gap, " ".join(phrases[i - 1][2][-2:]) + " | " + " ".join(t[:2])))
            a = max(a, pe + pad)  # never overlap previous segment
        seg = fade_edges(audio[int(a * SR):int(b * SR)].copy(), int(FADE * SR), int(fade_out * SR))
        segs.append((a, b))
        if i > 0:
            out.append(tone)
        out.append(seg)
    return np.concatenate(out) if out else audio, cut_log, segs


def probe_video(path):
    """Returns dict(fps_num, fps_den, width, height, frames, duration) or None if no video stream."""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=r_frame_rate,width,height,nb_frames,duration:format=duration",
                        "-of", "json", str(path)], capture_output=True)
    try:
        j = json.loads(p.stdout.decode())
        s = j["streams"][0]
    except (ValueError, KeyError, IndexError):
        return None
    n, d = (int(x) for x in s["r_frame_rate"].split("/"))
    dur = float(s.get("duration") or j["format"]["duration"])
    return {"num": n, "den": d or 1, "w": int(s["width"]), "h": int(s["height"]), "dur": dur}


def export_premiere_xml(xml_path, video, vinfo, audio_wav, segs, pause, name):
    """Final Cut Pro 7 XML (xmeml v4) that Premiere imports via File > Import.
    V1: the original video cut into the same pieces as the audio (each piece followed by `pause`
    seconds of footage, which is the length of the room-tone gap in the cleaned wav);
    A1/A2: the cleaned audio wav as one continuous clip, so sync is sample-accurate."""
    from urllib.parse import quote
    from xml.sax.saxutils import escape

    fps = vinfo["num"] / vinfo["den"]
    tb = int(round(fps))
    ntsc = "TRUE" if vinfo["den"] != 1 else "FALSE"
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"

    def url(p):
        return "file://localhost/" + quote(Path(p).resolve().as_posix(), safe="/")

    # timeline layout in seconds: piece i lasts (b-a) (+pause unless last)
    pieces, t = [], 0.0
    for i, (a, b) in enumerate(segs):
        ln = (b - a) + (pause if i < len(segs) - 1 else 0.0)
        pieces.append((a, a + ln, t, t + ln))
        t += ln
    total_frames = int(round(t * fps))
    vid_frames = int(round(vinfo["dur"] * fps))

    vfile = (f'<file id="file-v"><name>{escape(Path(video).name)}</name><pathurl>{url(video)}</pathurl>'
             f'{rate}<duration>{vid_frames}</duration><media><video><samplecharacteristics>{rate}'
             f'<width>{vinfo["w"]}</width><height>{vinfo["h"]}</height></samplecharacteristics>'
             f'</video></media></file>')
    clips = []
    for i, (s_in, s_out, t0, t1) in enumerate(pieces):
        st, en = int(round(t0 * fps)), int(round(t1 * fps))
        fin = int(round(s_in * fps))
        fin = min(fin, max(0, vid_frames - (en - st)))
        file_xml = vfile if i == 0 else '<file id="file-v"/>'
        clips.append(
            f'<clipitem id="v{i}"><name>{escape(Path(video).name)}</name><enabled>TRUE</enabled>'
            f'<duration>{vid_frames}</duration>{rate}<start>{st}</start><end>{en}</end>'
            f'<in>{fin}</in><out>{fin + (en - st)}</out>{file_xml}</clipitem>')

    def aclip(idx, ch):
        file_xml = (f'<file id="file-a"><name>{escape(Path(audio_wav).name)}</name>'
                    f'<pathurl>{url(audio_wav)}</pathurl>{rate}<duration>{total_frames}</duration>'
                    f'<media><audio><samplecharacteristics><depth>24</depth><samplerate>{SR}</samplerate>'
                    f'</samplecharacteristics><channelcount>{audio_ch}</channelcount></audio></media></file>'
                    if idx == 0 else '<file id="file-a"/>')
        return (f'<track><clipitem id="a{idx}"><name>{escape(Path(audio_wav).name)}</name>'
                f'<enabled>TRUE</enabled><duration>{total_frames}</duration>{rate}'
                f'<start>0</start><end>{total_frames}</end><in>0</in><out>{total_frames}</out>'
                f'{file_xml}<sourcetrack><mediatype>audio</mediatype><trackindex>{ch}</trackindex>'
                f'</sourcetrack></clipitem></track>')

    audio_ch = channel_count(audio_wav)
    atracks = "".join(aclip(i, i + 1) for i in range(audio_ch))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
        f'<sequence id="seq-1"><name>{escape(name)}</name><duration>{total_frames}</duration>{rate}'
        f'<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>'
        '<media><video><format><samplecharacteristics>' + rate +
        f'<width>{vinfo["w"]}</width><height>{vinfo["h"]}</height><anamorphic>FALSE</anamorphic>'
        '<pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance>'
        '</samplecharacteristics></format><track>' + "".join(clips) + '</track></video>'
        f'<audio><numOutputChannels>{audio_ch}</numOutputChannels><format><samplecharacteristics>'
        f'<depth>24</depth><samplerate>{SR}</samplerate></samplecharacteristics></format>'
        + atracks + '</audio></media></sequence></xmeml>')
    Path(xml_path).write_text(xml, encoding="utf-8")
    return len(pieces)


def write_wav_f32(path, x):
    ch = x.shape[1]
    p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", str(ch),
                        "-i", "-", "-c:a", "pcm_s24le", str(path)],
                       input=x.astype(np.float32).tobytes(), capture_output=True)
    if p.returncode != 0:
        sys.exit(p.stderr.decode(errors="replace"))


def enhance(src, dst, lufs, denoise, declick, compress, tp=-1.0, lra=11, eq=(0.0, 0.0, 0.0)):
    """Gentle by default: only a rumble filter and a *linear* gain to the target loudness.
    Compressor, declicker and denoiser are opt-in because they audibly change the tone."""
    chain = ["highpass=f=50"]
    low, mid, high = eq  # simple 3-band tone control, dB
    if low:
        chain.append(f"bass=g={low}:f=120:t=s:w=0.7")
    if mid:
        chain.append(f"equalizer=f=3000:t=q:w=0.9:g={mid}")
    if high:
        chain.append(f"treble=g={high}:f=8000:t=s:w=0.7")
    if declick:
        chain.insert(0, "adeclick=t=2:w=55:o=75")
    if denoise:
        chain.append("afftdn=nf=-28:nr=10:nt=w")
    if compress:
        chain += ["acompressor=threshold=-21dB:ratio=2:attack=20:release=250:makeup=2",
                  "alimiter=limit=0.95"]
    norm_chain(src, dst, ",".join(chain), lufs, tp, lra)


EXPAND = ("compand=attacks=0.004:decays=0.6:delay=0.04:soft-knee=8:"
          "points=-90/-118|-60/-82|-40/-54|-25/-33|-13/-13|0/0")  # approved "expand2" curve


def expand_pass(path, lufs, tp=-1.0, lra=18):
    """Widens the dynamic range: quieter syllables go down, loud ones stay (downward expansion
    calibrated for a file that is already at about -14 LUFS), then loudness is restored."""
    tmp = Path(path).with_name(Path(path).stem + ".tmp.wav")
    Path(path).replace(tmp)
    norm_chain(tmp, path, EXPAND, lufs, tp, lra)
    tmp.unlink()


def norm_chain(src, dst, base, lufs, tp, lra):
    # pass 1: measure
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af",
                        f"{base},loudnorm=I={lufs}:TP={tp}:LRA={lra}:print_format=json",
                        "-f", "null", "-"], capture_output=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr.decode(errors="replace"), re.S)
    ln = f"loudnorm=I={lufs}:TP={tp}:LRA={lra}"
    if m:
        j = json.loads(m.group(0))
        ln += (f":measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}"
               f":measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if str(dst).lower().endswith(".mp3") \
        else ["-c:a", "pcm_s24le"]
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", f"{base},{ln}",
         "-ar", "48000", *codec, str(dst)])


def write_transcript(audio16, model_name, lang, folder, stem):
    from faster_whisper import WhisperModel

    def go(device, compute):
        m = WhisperModel(model_name, device=device, compute_type=compute)
        segs, _ = m.transcribe(audio16, language=lang, vad_filter=True,
                               condition_on_previous_text=False)
        return list(segs)

    try:
        segs = go("auto", "auto")
    except RuntimeError:
        segs = go("cpu", "int8")

    def ts(t, sep):
        h, r = divmod(t, 3600)
        m, s = divmod(r, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d}{sep}{int((s % 1) * 1000):03d}"

    with open(folder / f"{stem}.transcript.txt", "w", encoding="utf-8") as f:
        f.write(" ".join(s.text.strip() for s in segs) + "\n\n--- по сегментам ---\n")
        for s in segs:
            f.write(f"[{ts(s.start, '.')} - {ts(s.end, '.')}] {s.text.strip()}\n")
    with open(folder / f"{stem}.srt", "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{ts(s.start, ',')} --> {ts(s.end, ',')}\n{s.text.strip()}\n\n")
    return len(segs)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--mode", choices=["vad", "whisper"], default="vad",
                    help="vad: cut by speech detection (default, safe);\n"
                         "whisper: cut by transcribed word timings (can drop passages Whisper skips)")
    ap.add_argument("--vad-threshold", type=float, default=0.5,
                    help="VAD sensitivity 0..1; raise (0.6-0.7) if breaths are kept, lower if speech is clipped")
    ap.add_argument("--model", default="small", help="whisper mode only: tiny/base/small/medium/large-v3")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--pause", type=float, default=0.35,
                    help="length a too-long gap is shortened to, s (default 0.35)")
    ap.add_argument("--keep-gap", type=float, default=0.45,
                    help="gaps up to this length stay untouched, s (default 0.45 = rhythm of the reference)")
    ap.add_argument("--pad", type=float, default=0.06, help="padding around words, s (default 0.06)")
    ap.add_argument("--lufs", type=float, default=-14.0, help="target loudness (default -14, YouTube)")
    ap.add_argument("--denoise", action="store_true", help="enable FFT noise reduction")
    ap.add_argument("--declick", action="store_true", help="enable adeclick (can dull consonants)")
    ap.add_argument("--compress", action="store_true", help="enable gentle compressor + limiter")
    ap.add_argument("--no-enhance", action="store_true", help="only cut, skip filters and loudnorm")
    ap.add_argument("--no-transcript", action="store_true", help="skip transcription")
    ap.add_argument("--audio-stream", type=int, default=None,
                    help="video input: index among audio tracks (default: the loudest one)")
    ap.add_argument("--no-render", action="store_true", help="video input: skip rendering the cut MP4")
    ap.add_argument("--fade-out", type=float, default=0.12,
                    help="smooth fade-out at the end of each phrase before a pause, s (default 0.12, 0 = off)")
    ap.add_argument("--keep-breaths", action="store_true",
                    help="do not trim breath-like tails after phrases (trimmed by default)")
    ap.add_argument("--eq-low", type=float, default=0.0, help="EQ: bass (120 Hz shelf), dB, + warmer / - thinner")
    ap.add_argument("--eq-mid", type=float, default=0.0, help="EQ: presence (3 kHz), dB, + clearer / - softer")
    ap.add_argument("--eq-high", type=float, default=0.0, help="EQ: treble (8 kHz shelf), dB, + brighter / - duller")
    ap.add_argument("--expand", action="store_true", help="wider loudness dynamics (downward expansion)")
    ap.add_argument("--tag", default="", help="suffix for an alternative version, e.g. _dynamic")
    ap.add_argument("--out-root", default=str(Path(__file__).parent / "projects"),
                    help="every input gets its own sub-folder here")
    a = ap.parse_args()

    src = Path(a.input)
    if not src.exists():
        sys.exit(f"File not found: {src}")
    stem = re.sub(r'[<>:"/\\|?*]', "_", src.stem)
    folder = Path(a.out_root) / stem
    folder.mkdir(parents=True, exist_ok=True)
    ext = ".mp3" if (a.output or "").lower().endswith(".mp3") else ".wav"
    dst = Path(a.output) if a.output else folder / f"{stem}_clean{a.tag}{ext}"
    print(f"Project folder: {folder}")

    print("Decoding...")
    vinfo = probe_video(src)
    stream = a.audio_stream if a.audio_stream is not None else pick_audio_stream(src)
    if vinfo:
        print(f"Audio stream used: a:{stream}")
    ch = channel_count(src, stream)
    audio = decode(src, SR, ch, stream)
    audio16 = decode(src, 16000, 1, stream)[:, 0]
    mono = audio.mean(axis=1)
    print(f"Duration: {len(audio)/SR:.1f} s, channels: {ch}")
    if a.mode == "vad":
        words = vad_segments(audio16, a.vad_threshold, 100)
        print(f"Speech regions (VAD): {len(words)}")
        if not a.keep_breaths:
            words, tr, dr = trim_breaths(audio16, words)
            print(f"Breaths: trimmed {tr:.1f} s of phrase tails, dropped {dr} breath-only regions")
    else:
        print(f"Transcribing ({a.model}), first run downloads the model...")
        words, _ = transcribe_words(audio16, a.model, a.lang)
        print(f"Words: {len(words)}")
    if not words:
        sys.exit("No speech found.")

    chans, cuts, segs = [], [], []
    for c in range(ch):
        o, cl, sg = assemble(audio[:, c].copy(), words, a.pause, a.pad, a.keep_gap, None, a.fade_out)
        chans.append(o)
        if c == 0:
            cuts, segs = cl, sg
    out = np.stack(chans, axis=1)
    print(f"Cut: {len(audio)/SR:.1f} s -> {len(out)/SR:.1f} s  (removed {(len(audio)-len(out))/SR:.1f} s)")

    report = folder / f"{stem}{a.tag}.cuts.txt"
    with open(report, "w", encoding="utf-8") as f:
        f.write("start\tend\tgap_s\n")
        for s, e, g, _ in cuts:
            f.write(f"{s:.2f}\t{e:.2f}\t{g:.2f}\n")
    print(f"Cut list: {report}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "cut.wav"
        write_wav_f32(tmp, out)
        if a.no_enhance:
            run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-c:a", "pcm_s24le", str(dst)])
        else:
            enhance(tmp, dst, a.lufs, a.denoise, a.declick, a.compress, eq=(a.eq_low, a.eq_mid, a.eq_high))
            if a.expand:
                expand_pass(dst, a.lufs)
    print(f"Done: {dst}")

    if vinfo:
        if str(dst).lower().endswith(".mp3"):
            print("Video input: use a .wav output for the XML/video export (skipped).")
        else:
            xml = folder / f"{stem}{a.tag}_premiere.xml"
            n = export_premiere_xml(xml, src, vinfo, dst, segs, a.pause, stem)
            print(f"Premiere XML: {xml} ({n} video pieces, {vinfo['num']}/{vinfo['den']} fps)")
            if not a.no_render:
                mp4 = folder / f"{stem}{a.tag}_cut.mp4"
                print("Rendering cut video...")
                render_cut_video(src, vinfo, dst, segs, a.pause, mp4)
                print(f"Cut video: {mp4}")

    if not a.no_transcript:
        print(f"Transcribing ({a.model}), first run downloads the model...")
        n = write_transcript(audio16, a.model, a.lang, folder, stem)
        print(f"Transcript: {n} segments -> {folder}")


if __name__ == "__main__":
    main()
