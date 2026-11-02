"""VoiceCleanKit: окно приложения. Запуск: 0_SETTINGS_AND_RUN.bat"""
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:  # перетаскивание файлов в окно (ставится установщиком, необязательно)
    from tkinterdnd2 import DND_FILES, TkinterDnD
    Base = TkinterDnD.Tk
except Exception:
    DND_FILES = None
    Base = tk.Tk

HERE = Path(__file__).parent
CFG = HERE / "settings.json"
PY = str(HERE / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable

# палитра
BG, CARD, CARD2, LINE = "#16181f", "#20232d", "#2a2e3a", "#343947"
FG, MUTED, ACCENT, ACCENT_H = "#e8eaf0", "#8d93a5", "#4f8cff", "#6aa0ff"
FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI Semibold", 10)
FONT_H = ("Segoe UI Semibold", 16)
FONT_S = ("Segoe UI", 9)

PRESETS = {
    "Плотно": dict(keep_gap=0.2, pause=0.22, vad=0.65, pad=0.06, fade_out=0.12, lufs=-14.0,
                   expand=True, denoise=False, compress=False, declick=False, breaths=False),
    "Мягко": dict(keep_gap=0.45, pause=0.35, vad=0.5, pad=0.06, fade_out=0.12, lufs=-14.0,
                  expand=False, denoise=False, compress=False, declick=False, breaths=False),
}
EQ_PRESETS = {"Без изменений": (0.0, 0.0, 0.0), "Теплее": (2.5, 0.0, 0.0), "Чётче": (0.0, 2.0, 1.0),
              "Как в OBS": (2.0, 0.0, 1.0)}
PRESET_HINT = {"Плотно": "Короткие паузы, динамичный монтаж (эталонные настройки).",
               "Мягко": "Естественный ритм речи, паузы длиннее."}
DEFAULT = dict(PRESETS["Плотно"], eq_low=0.0, eq_mid=0.0, eq_high=0.0, model="small", lang="ru", prompt="",
               transcript=True, render=True)

SLIDERS = [
    ("keep_gap", "Паузы не трогать до", "с", 0.0, 1.0, 0.01,
     "Паузы короче этого значения остаются как есть."),
    ("pause", "Длинные паузы сокращать до", "с", 0.05, 1.0, 0.01,
     "Во что превращается слишком длинная пауза."),
    ("vad", "Чувствительность речи", "", 0.2, 0.9, 0.01,
     "Выше: строже режет вздохи. Ниже: не съедает тихие окончания слов."),
    ("pad", "Запас вокруг слов", "с", 0.0, 0.3, 0.01, "Сколько тишины оставлять до и после слов."),
    ("fade_out", "Затухание конца фразы", "с", 0.0, 0.4, 0.01, "Плавный спад перед паузой. 0: выключено."),
    ("lufs", "Целевая громкость", "LUFS", -24.0, -8.0, 0.5, "Для YouTube: −14."),
]
EQ_SLIDERS = [
    ("eq_low", "Низ (тело голоса)", "дБ", -6.0, 6.0, 0.5, "Плюс: теплее и плотнее. Минус: тоньше. Область ниже 120 Гц."),
    ("eq_mid", "Середина (чёткость)", "дБ", -6.0, 6.0, 0.5, "Плюс: разборчивее речь. Минус: мягче. Область около 3 кГц."),
    ("eq_high", "Верх (воздух)", "дБ", -6.0, 6.0, 0.5, "Плюс: ярче и «воздушнее». Минус: глуше. Выше 8 кГц."),
]
SWITCHES = [
    ("expand", "Приглушать тихие места между словами", "Фон между словами тише, речь не меняется."),
    ("denoise", "Шумоподавление", "Только при заметном фоновом шуме: может задеть тембр."),
    ("compress", "Мягкий компрессор", "Выравнивает громкость, но делает звук плотнее."),
    ("declick", "Убирать щелчки", "Может притупить согласные."),
    ("breaths", "Сохранять вдохи в конце фраз", "По умолчанию вдохи обрезаются."),
]


def load():
    cfg = dict(DEFAULT)
    try:
        cfg.update(json.loads(CFG.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def style_app(root):
    s = ttk.Style(root)
    s.theme_use("clam")
    root.configure(bg=BG)
    s.configure(".", background=BG, foreground=FG, font=FONT, bordercolor=LINE, focuscolor=BG)
    s.configure("TFrame", background=BG)
    s.configure("Card.TFrame", background=CARD)
    s.configure("TLabel", background=BG, foreground=FG)
    s.configure("Card.TLabel", background=CARD, foreground=FG)
    s.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=FONT_S)
    s.configure("Head.TLabel", background=BG, foreground=FG, font=FONT_H)
    s.configure("Sub.TLabel", background=BG, foreground=MUTED, font=FONT_S)
    s.configure("Val.TLabel", background=CARD, foreground=ACCENT_H, font=FONT_B)
    s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
    s.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(18, 9), borderwidth=0, font=FONT_B)
    s.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", FG)])
    s.configure("TButton", background=CARD2, foreground=FG, padding=(14, 8), borderwidth=0, font=FONT)
    s.map("TButton", background=[("active", LINE), ("disabled", CARD)], foreground=[("disabled", MUTED)])
    s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", font=FONT_B, padding=(18, 9))
    s.map("Accent.TButton", background=[("active", ACCENT_H), ("disabled", CARD2)])
    s.configure("Preset.TButton", background=CARD2, foreground=FG, padding=(16, 7))
    s.configure("PresetOn.TButton", background=ACCENT, foreground="#ffffff", padding=(16, 7), font=FONT_B)
    s.map("PresetOn.TButton", background=[("active", ACCENT_H)])
    s.configure("Horizontal.TScale", background=CARD, troughcolor=CARD2, bordercolor=CARD, lightcolor=ACCENT,
                darkcolor=ACCENT)
    s.configure("TCheckbutton", background=CARD, foreground=FG, indicatorbackground=CARD2,
                indicatorforeground="#ffffff", font=FONT)
    s.map("TCheckbutton", background=[("active", CARD)], indicatorbackground=[("selected", ACCENT)])
    s.configure("TEntry", fieldbackground=CARD2, foreground=FG, insertcolor=FG, bordercolor=LINE, padding=7)
    s.configure("TCombobox", fieldbackground=CARD2, background=CARD2, foreground=FG, arrowcolor=FG,
                bordercolor=LINE, padding=6)
    s.map("TCombobox", fieldbackground=[("readonly", CARD2)], foreground=[("readonly", FG)])
    root.option_add("*TCombobox*Listbox.background", CARD2)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    s.configure("Horizontal.TProgressbar", troughcolor=CARD2, background=ACCENT, bordercolor=BG,
                lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)


class App(Base):
    def __init__(self):
        super().__init__()
        self.title("VoiceCleanKit")
        self.geometry("980x930")
        self.minsize(900, 860)
        style_app(self)
        cfg = load()
        self.vars = {}
        self.proc = None
        self.preset_btns = {}
        self.last_folder = None

        # шапка
        head = ttk.Frame(self, padding=(24, 18, 24, 6))
        head.pack(fill="x")
        ttk.Label(head, text="VoiceCleanKit", style="Head.TLabel").pack(anchor="w")
        ttk.Label(head, text="Очистка голоса для видео: паузы, вздохи, громкость и субтитры",
                  style="Sub.TLabel").pack(anchor="w")

        # нижняя панель (создаётся первой, чтобы не обрезалась)
        bar = ttk.Frame(self, padding=(24, 12, 24, 18))
        bar.pack(side="bottom", fill="x")
        self.status = tk.StringVar(value="Выберите файл")
        ttk.Label(bar, textvariable=self.status, style="Sub.TLabel").grid(row=0, column=0, sticky="w")
        self.pb = ttk.Progressbar(bar, mode="indeterminate", length=200)
        self.pb.grid(row=1, column=0, sticky="we", pady=(6, 0))
        bar.columnconfigure(0, weight=1)
        btns = ttk.Frame(bar)
        btns.grid(row=0, column=1, rowspan=2, padx=(16, 0))
        self.b_run = ttk.Button(btns, text="Обработать голос", style="Accent.TButton",
                                command=lambda: self.run("clean"))
        self.b_srt = ttk.Button(btns, text="Только субтитры", command=lambda: self.run("srt"))
        self.b_stop = ttk.Button(btns, text="Стоп", command=self.stop)
        self.b_open = ttk.Button(btns, text="Папка результата", command=self.open_results)
        self.b_run.pack(side="left")
        self.b_srt.pack(side="left", padx=8)
        self.b_open.pack(side="left")

        # карточка файла / зона перетаскивания
        self.file = tk.StringVar()
        self.drop = tk.Label(self, text="Перетащите сюда аудио или видео\nили нажмите, чтобы выбрать файл",
                             bg=CARD, fg=MUTED, font=FONT, height=4, cursor="hand2",
                             highlightthickness=1, highlightbackground=LINE)
        self.drop.pack(fill="x", padx=24, pady=(10, 8))
        self.drop.bind("<Button-1>", lambda e: self.pick())
        if DND_FILES:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self.on_drop)
        else:
            self.drop.configure(text="Нажмите, чтобы выбрать аудио или видео файл")

        # вкладки
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=24, pady=(4, 0))
        self.tab_voice = ttk.Frame(self.nb, style="Card.TFrame", padding=20)
        self.tab_eq = ttk.Frame(self.nb, style="Card.TFrame", padding=20)
        self.tab_srt = ttk.Frame(self.nb, style="Card.TFrame", padding=20)
        self.tab_log = ttk.Frame(self.nb, style="Card.TFrame", padding=12)
        self.nb.add(self.tab_voice, text="Голос")
        self.nb.add(self.tab_eq, text="Эквалайзер")
        self.nb.add(self.tab_srt, text="Расшифровка")
        self.nb.add(self.tab_log, text="Журнал")
        self.build_voice(cfg)
        self.build_eq(cfg)
        self.build_srt(cfg)
        self.build_log()
        self.mark_preset(cfg)

    # ---- вкладки ----
    def slider_rows(self, parent, items, cfg, on_change=None):
        grid = ttk.Frame(parent, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        for i, (k, label, unit, lo, hi, step, tip) in enumerate(items):
            v = tk.DoubleVar(value=cfg[k])
            self.vars[k] = v
            ttk.Label(grid, text=label, style="Card.TLabel").grid(row=i * 2, column=0, sticky="w", pady=(8, 0))
            ttk.Scale(grid, from_=lo, to=hi, variable=v, style="Horizontal.TScale",
                      command=lambda _x, v=v, s=step: (v.set(round(round(v.get() / s) * s, 3)),
                                                       on_change() if on_change else None)
                      ).grid(row=i * 2, column=1, sticky="we", padx=16, pady=(8, 0))
            ttk.Label(grid, textvariable=v, style="Val.TLabel", width=7, anchor="e").grid(row=i * 2, column=2)
            ttk.Label(grid, text=unit, style="Muted.TLabel", width=5).grid(row=i * 2, column=3, sticky="w")
            ttk.Label(grid, text=tip, style="Muted.TLabel").grid(row=i * 2 + 1, column=0, columnspan=4, sticky="w")

    def build_voice(self, cfg):
        t = self.tab_voice
        pf = ttk.Frame(t, style="Card.TFrame")
        pf.pack(fill="x", pady=(0, 14))
        ttk.Label(pf, text="Пресет", style="Card.TLabel", font=FONT_B).pack(side="left", padx=(0, 12))
        for n in PRESETS:
            b = ttk.Button(pf, text=n, style="Preset.TButton", command=lambda n=n: self.apply_preset(n))
            b.pack(side="left", padx=(0, 6))
            self.preset_btns[n] = b
        self.preset_hint = tk.StringVar()
        ttk.Label(pf, textvariable=self.preset_hint, style="Muted.TLabel").pack(side="left", padx=10)

        self.slider_rows(t, SLIDERS, cfg, self.mark_preset)

        ttk.Separator(t).pack(fill="x", pady=14)
        sw = ttk.Frame(t, style="Card.TFrame")
        sw.pack(fill="x")
        for i, (k, label, tip) in enumerate(SWITCHES):
            v = tk.BooleanVar(value=cfg[k])
            v.trace_add("write", lambda *_: self.mark_preset())
            self.vars[k] = v
            ttk.Checkbutton(sw, text=label, variable=v).grid(row=i, column=0, sticky="w", pady=(3, 0))
            ttk.Label(sw, text=tip, style="Muted.TLabel").grid(row=i, column=1, sticky="w", padx=14)
        self.vars["transcript"] = tk.BooleanVar(value=cfg["transcript"])
        self.vars["render"] = tk.BooleanVar(value=cfg["render"])
        ttk.Checkbutton(sw, text="Делать расшифровку вместе с обработкой", variable=self.vars["transcript"]
                        ).grid(row=len(SWITCHES), column=0, sticky="w", pady=(10, 0))
        ttk.Checkbutton(sw, text="Для видео: собрать нарезанный MP4", variable=self.vars["render"]
                        ).grid(row=len(SWITCHES) + 1, column=0, sticky="w", pady=(3, 0))

    def build_eq(self, cfg):
        t = self.tab_eq
        ttk.Label(t, text="Простая тонкая настройка тембра. Ноль везде: звук без изменений.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        pf = ttk.Frame(t, style="Card.TFrame")
        pf.pack(fill="x", pady=(0, 10))
        for name, vals in EQ_PRESETS.items():
            ttk.Button(pf, text=name, style="Preset.TButton",
                       command=lambda v=vals: [self.vars[k].set(x) for k, x in zip(("eq_low", "eq_mid", "eq_high"), v)]
                       ).pack(side="left", padx=(0, 6))
        self.slider_rows(t, EQ_SLIDERS, cfg)

    def build_srt(self, cfg):
        t = self.tab_srt
        for k in ("model", "lang", "prompt"):
            self.vars[k] = tk.StringVar(value=cfg[k])
        ttk.Label(t, text="Модель распознавания", style="Card.TLabel", font=FONT_B).grid(row=0, column=0, sticky="w")
        ttk.Combobox(t, textvariable=self.vars["model"], width=14, state="readonly",
                     values=["tiny", "base", "small", "medium", "large-v3"]).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(t, text="small: быстро и достаточно точно. large-v3: точнее, но медленнее и тяжелее.",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 14))
        ttk.Label(t, text="Язык", style="Card.TLabel", font=FONT_B).grid(row=0, column=1, sticky="w", padx=(24, 0))
        ttk.Entry(t, textvariable=self.vars["lang"], width=6).grid(row=1, column=1, sticky="w", padx=(24, 0), pady=(4, 0))
        ttk.Label(t, text="Названия и термины", style="Card.TLabel", font=FONT_B).grid(row=3, column=0, sticky="w")
        ttk.Entry(t, textvariable=self.vars["prompt"]).grid(row=4, column=0, columnspan=3, sticky="we", pady=(4, 0))
        ttk.Label(t, text="Через запятую: названия игр, имена, термины. Помогает Whisper писать их правильно.",
                  style="Muted.TLabel").grid(row=5, column=0, columnspan=3, sticky="w", pady=(2, 0))
        t.columnconfigure(2, weight=1)

    def build_log(self):
        self.log = tk.Text(self.tab_log, bg=BG, fg=FG, insertbackground=FG, relief="flat", wrap="word",
                           font=("Consolas", 9), state="disabled", padx=10, pady=8)
        self.log.pack(fill="both", expand=True)

    # ---- пресеты ----
    def apply_preset(self, name):
        for k, v in PRESETS[name].items():
            self.vars[k].set(v)
        self.mark_preset()

    def mark_preset(self, cfg=None):
        cur = cfg or {k: self.vars[k].get() for k in PRESETS["Плотно"] if k in self.vars}
        hit = None
        for n, p in PRESETS.items():
            if all((bool(cur[k]) == v) if isinstance(v, bool) else abs(float(cur[k]) - float(v)) < 1e-6
                   for k, v in p.items() if k in cur):
                hit = n
        for n, b in self.preset_btns.items():
            b.configure(style="PresetOn.TButton" if n == hit else "Preset.TButton")
        self.preset_hint.set(PRESET_HINT[hit] if hit else "Свои настройки")

    # ---- файл ----
    def on_drop(self, e):
        files = self.tk.splitlist(e.data)
        if files:
            self.set_file(files[0])

    def pick(self):
        f = filedialog.askopenfilename(filetypes=[("Аудио/видео", "*.wav *.mp3 *.m4a *.flac *.mp4 *.mov *.mkv *.avi"),
                                                  ("Все", "*.*")])
        if f:
            self.set_file(f)

    def set_file(self, f):
        self.file.set(f)
        self.drop.configure(text=Path(f).name + "\n" + str(Path(f).parent), fg=FG)
        self.status.set("Готово к запуску")

    # ---- запуск ----
    def values(self):
        return {k: v.get() for k, v in self.vars.items()}

    def save(self):
        CFG.write_text(json.dumps(self.values(), ensure_ascii=False, indent=2), encoding="utf-8")

    def say(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")

    def command(self, mode):
        p = self.values()
        f = self.file.get().strip()
        if mode == "srt":
            cmd = [PY, "make_srt.py", f, "--model", p["model"], "--lang", p["lang"]]
            if p["prompt"].strip():
                cmd += ["--prompt", p["prompt"].strip()]
            return cmd
        cmd = [PY, "voice_clean.py", f, "--keep-gap", str(p["keep_gap"]), "--pause", str(p["pause"]),
               "--vad-threshold", str(p["vad"]), "--pad", str(p["pad"]), "--fade-out", str(p["fade_out"]),
               "--lufs", str(p["lufs"]),
               "--eq-low", str(p["eq_low"]), "--eq-mid", str(p["eq_mid"]), "--eq-high", str(p["eq_high"]),
               "--model", p["model"], "--lang", p["lang"]]
        for flag, key in [("--expand", "expand"), ("--denoise", "denoise"), ("--compress", "compress"),
                          ("--declick", "declick"), ("--keep-breaths", "breaths")]:
            if p[key]:
                cmd.append(flag)
        if not p["transcript"]:
            cmd.append("--no-transcript")
        if not p["render"]:
            cmd.append("--no-render")
        return cmd

    def busy(self, on):
        for b in (self.b_run, self.b_srt):
            b.state(["disabled"] if on else ["!disabled"])
        if on:
            self.b_stop.pack(side="left", padx=(8, 0))
            self.pb.start(12)
        else:
            self.b_stop.pack_forget()
            self.pb.stop()

    def run(self, mode):
        f = self.file.get().strip()
        if not f or not Path(f).exists():
            messagebox.showwarning("Файл", "Сначала выберите файл.")
            return
        self.save()
        cmd = self.command(mode)
        self.say("\n> " + " ".join(cmd[1:]) + "\n")
        self.nb.select(self.tab_log)
        self.status.set("Идёт обработка…" if mode == "clean" else "Идёт расшифровка…")
        self.busy(True)
        self.last_folder = HERE / "projects" / Path(f).stem

        def work():
            env = dict(os.environ, PYTHONUTF8="1")
            try:
                self.proc = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for line in self.proc.stdout:
                    self.after(0, self.say, line)
                    ln = line.strip()
                    if ln:
                        self.after(0, self.status.set, ln[:110])
                self.proc.wait()
                ok = self.proc.returncode == 0
            except Exception as e:
                self.after(0, self.say, f"\nОшибка запуска: {e}\n")
                ok = False
            self.proc = None
            self.after(0, self.done, ok)
        threading.Thread(target=work, daemon=True).start()

    def done(self, ok):
        self.busy(False)
        self.status.set("Готово. Результат в папке projects" if ok else "Остановлено или ошибка, см. журнал")
        self.say("\nГотово.\n" if ok else "\nОшибка или остановка.\n")
        if ok:
            self.open_results()

    def stop(self):
        if self.proc:
            self.proc.terminate()

    def open_results(self):
        d = self.last_folder if self.last_folder and self.last_folder.exists() else HERE / "projects"
        d.mkdir(exist_ok=True)
        os.startfile(d)


if __name__ == "__main__":
    App().mainloop()
