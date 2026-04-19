#!/usr/bin/env python3
"""
Mini Video Editor — v9
──────────────────────
Features:
  • Upload multiple videos (appended as one timeline)
  • Upload audio files (mp3, wav, aac, ogg, flac, m4a, …)
      – Replace video audio OR mix with original on export
  • Scrub timeline with draggable cursor overlay
  • Mark In / Mark Out  →  up to 5 named segments
  • Text overlay  – font size, colour swatches, 3×3 position grid, BG opacity, timing
  • Logo overlay  – PNG/JPG upload, position grid, opacity, timing
  • Live preview  with overlays rendered on the actual frame
  • 6 export modes:
      merged · separate clips · no audio · audio only · 9:16 crop · 9:16 crop (separate)
  • Smooth output: yuv420p / baseline / faststart for universal playback

Requirements:
    pip install moviepy pillow numpy pygame
"""

from __future__ import annotations

import os
import sys
import time
import threading
import tempfile
from typing import Optional

# ── Dependency check ────────────────────────────────────────────────────────
_missing = []
try:
    import numpy as np
except ImportError:
    _missing.append("numpy")
try:
    from PIL import Image, ImageTk, ImageDraw, ImageFont
except ImportError:
    _missing.append("Pillow")
try:
    try:
        from moviepy import (
            VideoFileClip, concatenate_videoclips,
            CompositeVideoClip, ImageClip,
            AudioFileClip, concatenate_audioclips, CompositeAudioClip,
        )
    except ImportError:
        from moviepy.editor import (
            VideoFileClip, concatenate_videoclips,
            CompositeVideoClip, ImageClip,
            AudioFileClip, concatenate_audioclips, CompositeAudioClip,
        )
except ImportError:
    _missing.append("moviepy")

if _missing:
    python_exe = sys.executable
    install_cmd = f'"{python_exe}" -m pip install ' + " ".join(_missing)
    print("=" * 60)
    print("Missing:", ", ".join(_missing))
    print("Run:", install_cmd)
    print("=" * 60)
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _r = _tk.Tk(); _r.withdraw()
        _mb.showerror("Missing dependencies",
                      f"Packages missing: {', '.join(_missing)}\n\n"
                      f"Run this EXACT command:\n\n  {install_cmd}\n\nThen restart.")
        _r.destroy()
    except Exception:
        pass
    sys.exit(1)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ══════════════════════════════════════════════════════════════════════════════
#  COLOUR PALETTE
# ══════════════════════════════════════════════════════════════════════════════
BG       = "#ffffff"
BG2      = "#f5f4f1"
BG3      = "#eceae4"
BORDER   = "#dddbd4"
ACCENT   = "#185FA5"
TEXT     = "#1a1a1a"
TEXT2    = "#666666"
TEXT3    = "#aaaaaa"
GREEN    = "#1D9E75"
ORANGE   = "#D85A30"
PURPLE   = "#534AB7"
RED_C    = "#E24B4A"
SEG_COLS = [ACCENT, GREEN, ORANGE, PURPLE, RED_C]
SWATCH_COLORS = ["#ffffff", "#ffdd57", "#ff6b6b", "#74c69d", "#222222"]

# ffmpeg flags for maximum compatibility
_FFMPEG_COMPAT = ["-pix_fmt", "yuv420p",
                  "-movflags", "+faststart",
                  "-profile:v", "baseline",
                  "-level", "3.0"]


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def fmt(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return "00:00.000"
    m  = int(seconds // 60)
    s  = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{m:02d}:{s:02d}.{ms:03d}"


def parse_time(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s or s.lower() == "end":
        return None
    try:
        if ":" in s:
            parts = s.split(":")
            mins = int(parts[0])
            rest = parts[1]
            if "." in rest:
                sec_part, ms_part = rest.split(".")
                secs = int(sec_part) + int(ms_part.ljust(3, "0")[:3]) / 1000.0
            else:
                secs = float(rest)
            return mins * 60.0 + secs
        return float(s)
    except Exception:
        return None


def load_font(size: int):
    candidates = [
        "arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def overlay_xy(pos_idx, fw, fh, ow, oh, pad=16):
    col = pos_idx % 3
    row = pos_idx // 3
    x = pad if col == 0 else ((fw - ow) // 2 if col == 1 else fw - ow - pad)
    y = pad if row == 0 else ((fh - oh) // 2 if row == 1 else fh - oh - pad)
    return max(0, x), max(0, y)


def _do_mobile_crop(clip):
    """Crop a clip to 9:16 aspect ratio."""
    fw, fh = clip.size
    target_w = int(fh * 9 / 16)
    if target_w <= fw:
        x1 = (fw - target_w) // 2
        return clip.cropped(x1=x1, x2=x1 + target_w)
    else:
        target_h = int(fw * 16 / 9)
        y1 = (fh - target_h) // 2
        return clip.cropped(y1=y1, y2=y1 + target_h)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════
class VideoEditor:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Mini Video Editor")
        self.root.geometry("1260x920")
        self.root.configure(bg=BG2)
        self.root.minsize(980, 700)

        # ── Video state ────────────────────────────────────────────────────
        self.clip: Optional[VideoFileClip] = None
        self.video_paths: list[str] = []   # ← multi-video list
        self.video_path  = ""              # currently previewed file
        self.duration    = 0.0
        self.current_t   = 0.0
        self.is_playing  = False
        self._stop_evt   = threading.Event()
        self._pv_photo   = None

        # ── Scrubber cursor drag state ─────────────────────────────────────
        self._scrub_dragging = False

        # ── Segment state ──────────────────────────────────────────────────
        self.segments: list[dict] = []

        # ── Overlay state ──────────────────────────────────────────────────
        self.text_color   = "#ffffff"
        self.text_pos_idx = 7
        self.logo_path    = ""
        self.logo_pos_idx = 0
        self._logo_thumb  = None

        # ── Audio state ────────────────────────────────────────────────────
        self.audio_paths: list[str] = []
        self.audio_mode_var = tk.StringVar(value="replace")  # "replace" | "mix"

        # ── Export ─────────────────────────────────────────────────────────
        self.export_var = tk.StringVar(value="merged")

        self._apply_styles()
        self._build_ui()

    # ── Styles ────────────────────────────────────────────────────────────────
    def _apply_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TFrame",     background=BG)
        s.configure("TLabel",     background=BG, foreground=TEXT, font=("Helvetica", 12))
        s.configure("TScale",     background=BG, troughcolor=BG3, sliderthickness=14)
        s.configure("TScrollbar", troughcolor=BG2, background=BG3, arrowcolor=TEXT3)

    # ── Widget factories ──────────────────────────────────────────────────────
    def _card(self, parent, title, right_widget_cb=None):
        outer = tk.Frame(parent, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        hdr   = tk.Frame(outer, bg=BG)
        hdr.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(hdr, text=title.upper(), bg=BG, fg=TEXT2,
                 font=("Helvetica", 9, "bold")).pack(side="left")
        if right_widget_cb:
            right_widget_cb(hdr)
        tk.Frame(outer, bg=BORDER, height=1).pack(fill="x", pady=(6, 0))
        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=10)
        return outer, body

    def _accent_btn(self, parent, text, cmd, bg=ACCENT, fg="white", fill=False, **kw):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=bg, fg=fg, activebackground="#0C447C",
                      activeforeground="white" if bg == ACCENT else TEXT,
                      relief="flat", bd=0, cursor="hand2",
                      font=("Helvetica", 11), padx=12, pady=5)
        if fill:
            b.pack(fill="x", **kw)
        else:
            b.pack(**kw)
        return b

    def _outline_btn(self, parent, text, cmd, bg=BG2, fg=TEXT, **kw):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=bg, fg=fg, activebackground=BG3,
                      relief="flat", bd=0, cursor="hand2",
                      highlightthickness=1, highlightbackground=BORDER,
                      font=("Helvetica", 11), padx=12, pady=5)
        b.pack(**kw)
        return b

    def _label(self, parent, text, fg=TEXT2, font=("Helvetica", 10), **kw):
        l = tk.Label(parent, text=text, bg=BG, fg=fg, font=font)
        l.pack(**kw)
        return l

    def _scrollable(self, parent):
        c  = tk.Canvas(parent, bg=BG2, bd=0, highlightthickness=0)
        sb = tk.Scrollbar(parent, orient="vertical", command=c.yview)
        f  = tk.Frame(c, bg=BG2)
        f.bind("<Configure>", lambda _e: c.configure(scrollregion=c.bbox("all")))
        c.create_window((0, 0), window=f, anchor="nw")
        c.configure(yscrollcommand=sb.set)
        c.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        def _wheel(e):
            c.yview_scroll(int(-1 * (e.delta / 120)), "units")
        c.bind("<MouseWheel>", _wheel)
        f.bind("<MouseWheel>", _wheel)
        return c, f

    def _pos_grid(self, parent, default, callback):
        btns = []
        for i in range(9):
            r, col = divmod(i, 3)
            b = tk.Button(parent, width=2, height=1,
                          bg=ACCENT if i == default else BG3,
                          relief="flat", bd=0, cursor="hand2",
                          highlightthickness=1, highlightbackground=BORDER,
                          command=lambda x=i: callback(x))
            b.grid(row=r, column=col, padx=1, pady=1)
            btns.append(b)
        return btns

    # ── Top-level layout ──────────────────────────────────────────────────────
    def _build_ui(self):
        bar = tk.Frame(self.root, bg=BG, height=54,
                       highlightthickness=1, highlightbackground=BORDER)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        tk.Label(bar, text="Mini Video Editor",
                 bg=BG, fg=TEXT, font=("Helvetica", 14, "bold")
                 ).pack(side="left", padx=16, pady=12)

        # Multi-video buttons
        self._accent_btn(bar, "+ Add Video(s)", self.upload_video,
                         side="right", padx=14, pady=8)
        self._outline_btn(bar, "Clear All", self.clear_videos,
                          side="right", padx=6, pady=8)
        self._accent_btn(bar, "+ Add Audio", self.upload_audio,
                         bg=GREEN, side="right", padx=6, pady=8)

        self.file_lbl = tk.Label(bar, text="No video loaded",
                                  bg=BG, fg=TEXT3, font=("Helvetica", 11))
        self.file_lbl.pack(side="right", padx=4)

        body = tk.Frame(self.root, bg=BG2)
        body.pack(fill="both", expand=True)

        left_outer = tk.Frame(body, bg=BG2)
        left_outer.pack(side="left", fill="both", expand=True, padx=(12, 6), pady=12)
        _, self.left_f = self._scrollable(left_outer)

        right_outer = tk.Frame(body, bg=BG2, width=305)
        right_outer.pack(side="right", fill="y", padx=(6, 12), pady=12)
        right_outer.pack_propagate(False)
        _, self.right_f = self._scrollable(right_outer)

        self._build_left()
        self._build_right()

    # ── Left column ───────────────────────────────────────────────────────────
    def _build_left(self):
        self._build_preview_card(self.left_f)
        self._build_video_list_card(self.left_f)
        self._build_audio_card(self.left_f)
        self._build_text_card(self.left_f)
        self._build_logo_card(self.left_f)

    # ── Preview card ──────────────────────────────────────────────────────────
    def _build_preview_card(self, parent):
        def _hdr_right(hdr):
            self.file_badge = tk.Label(hdr, text="", bg=BG, fg=TEXT3,
                                       font=("Helvetica", 10))
            self.file_badge.pack(side="right")

        outer, body = self._card(parent, "Preview", _hdr_right)
        outer.pack(fill="x", pady=(0, 10))

        # Canvas
        self.pv = tk.Canvas(body, bg="#1a1a2e", bd=0, highlightthickness=0, height=280)
        self.pv.pack(fill="x")
        self.pv.create_text(320, 140, text="Upload a video to preview",
                             fill="#666699", font=("Helvetica", 12),
                             tags="placeholder")

        # Playback controls
        ctrl = tk.Frame(body, bg=BG)
        ctrl.pack(fill="x", pady=(8, 2))
        self.play_btn = self._outline_btn(ctrl, "▶  Play", self.toggle_play, side="left")
        self.timecode_lbl = tk.Label(ctrl, text="00:00.000 / 00:00.000",
                                      bg=BG, fg=TEXT2, font=("Courier", 11))
        self.timecode_lbl.pack(side="left", padx=10)

        # ── Custom scrubber with draggable cursor ──────────────────────────
        scrub_frame = tk.Frame(body, bg=BG)
        scrub_frame.pack(fill="x", pady=(6, 0))

        self.scrub_canvas = tk.Canvas(scrub_frame, bg=BG3, height=24,
                                       bd=0, highlightthickness=1,
                                       highlightbackground=BORDER,
                                       cursor="sb_h_double_arrow")
        self.scrub_canvas.pack(fill="x")
        self.scrub_var = tk.DoubleVar(value=0.0)

        # Draw initial track
        self._scrub_cursor_id = None
        self._draw_scrub_track(0.0)

        self.scrub_canvas.bind("<ButtonPress-1>",   self._scrub_click)
        self.scrub_canvas.bind("<B1-Motion>",        self._scrub_drag)
        self.scrub_canvas.bind("<ButtonRelease-1>",  self._scrub_release)
        self.scrub_canvas.bind("<Configure>",        lambda e: self._draw_scrub_track(self.current_t))

        # Time ticks
        tf = tk.Frame(body, bg=BG)
        tf.pack(fill="x", pady=(2, 0))
        self._tick_lbls = []
        for _ in range(5):
            l = tk.Label(tf, text="", bg=BG, fg=TEXT3, font=("Helvetica", 9))
            l.pack(side="left", expand=True)
            self._tick_lbls.append(l)

        # Mark In / Out
        mrow = tk.Frame(body, bg=BG)
        mrow.pack(fill="x", pady=(12, 6))
        self._outline_btn(mrow, "[ Mark In",  self.mark_in,
                          bg="#EAF3DE", fg="#3B6D11", side="left")
        tk.Frame(mrow, bg=BG, width=8).pack(side="left")
        self._outline_btn(mrow, "Mark Out ]", self.mark_out,
                          bg="#FAECE7", fg="#993C1D", side="left")

        # In / Out fields
        io = tk.Frame(body, bg=BG)
        io.pack(fill="x")
        io.columnconfigure(0, weight=1)
        io.columnconfigure(1, weight=1)
        tk.Label(io, text="In point",  bg=BG, fg=TEXT2, font=("Helvetica", 10)
                 ).grid(row=0, column=0, sticky="w")
        tk.Label(io, text="Out point", bg=BG, fg=TEXT2, font=("Helvetica", 10)
                 ).grid(row=0, column=1, sticky="w")
        self.in_var  = tk.StringVar(value="00:00.000")
        self.out_var = tk.StringVar(value="00:00.000")
        for col, var in ((0, self.in_var), (1, self.out_var)):
            e = tk.Entry(io, textvariable=var,
                         bg=BG2, fg=TEXT, insertbackground=TEXT,
                         font=("Courier", 12), bd=0,
                         highlightthickness=1, highlightbackground=BORDER,
                         relief="flat")
            e.grid(row=1, column=col, sticky="ew",
                   padx=(0, 6) if col == 0 else (0, 0), pady=4, ipady=5)
            e.bind("<Return>",   self._recalc_dur)
            e.bind("<FocusOut>", self._recalc_dur)

        self.dur_var = tk.StringVar(value="Duration: —")
        tk.Label(body, textvariable=self.dur_var, bg=BG, fg=TEXT2,
                 font=("Helvetica", 11)).pack(anchor="w", pady=(3, 10))
        self._accent_btn(body, "+ Add Segment", self.add_segment,
                         anchor="w", pady=(0, 4))

    # ── Custom scrubber drawing & interaction ─────────────────────────────────
    def _draw_scrub_track(self, t: float):
        c = self.scrub_canvas
        w = max(c.winfo_width(), 100)
        h = 24
        c.delete("all")

        # Track background
        c.create_rectangle(0, 0, w, h, fill=BG3, outline="")

        # Filled portion
        frac = (t / self.duration) if self.duration > 0 else 0.0
        frac = max(0.0, min(1.0, frac))
        fill_x = int(frac * w)
        if fill_x > 0:
            c.create_rectangle(0, 0, fill_x, h, fill=ACCENT, outline="")

        # Segment markers on track
        for i, seg in enumerate(self.segments):
            col = SEG_COLS[i % len(SEG_COLS)]
            x0 = int((seg["in"]  / self.duration) * w) if self.duration > 0 else 0
            x1 = int((seg["out"] / self.duration) * w) if self.duration > 0 else 0
            c.create_rectangle(x0, h - 5, max(x0 + 2, x1), h, fill=col, outline="")

        # Cursor handle
        cx = fill_x
        c.create_line(cx, 0, cx, h, fill="white", width=2)
        c.create_oval(cx - 6, 4, cx + 6, h - 4, fill="white",
                      outline=ACCENT, width=2)

    def _scrub_x_to_t(self, x: int) -> float:
        w = max(self.scrub_canvas.winfo_width(), 1)
        frac = max(0.0, min(1.0, x / w))
        return frac * self.duration

    def _scrub_click(self, e):
        if not self.clip:
            return
        self._scrub_dragging = True
        t = self._scrub_x_to_t(e.x)
        self._apply_scrub(t)

    def _scrub_drag(self, e):
        if not self.clip or not self._scrub_dragging:
            return
        t = self._scrub_x_to_t(e.x)
        self._apply_scrub(t)

    def _scrub_release(self, e):
        self._scrub_dragging = False

    def _apply_scrub(self, t: float):
        self.current_t = t
        self.scrub_var.set(t)
        self.timecode_lbl.configure(text=f"{fmt(t)} / {fmt(self.duration)}")
        self._draw_scrub_track(t)
        self._show_frame(t)

    # ── Video list card ───────────────────────────────────────────────────────
    def _build_video_list_card(self, parent):
        outer, body = self._card(parent, "Loaded Videos")
        outer.pack(fill="x", pady=(0, 10))
        self.video_list_body = body
        self._video_list_rows: list[tk.Widget] = []
        self._refresh_video_list()

    def _refresh_video_list(self):
        for w in self._video_list_rows:
            w.destroy()
        self._video_list_rows.clear()

        if not self.video_paths:
            lbl = tk.Label(self.video_list_body,
                           text="No videos loaded — click '+ Add Video(s)' above",
                           bg=BG, fg=TEXT3, font=("Helvetica", 10))
            lbl.pack(anchor="w", pady=4)
            self._video_list_rows.append(lbl)
            return

        for i, vp in enumerate(self.video_paths):
            row = tk.Frame(self.video_list_body, bg=BG2,
                           highlightthickness=1, highlightbackground=BORDER)
            row.pack(fill="x", pady=(0, 4))
            self._video_list_rows.append(row)

            inner = tk.Frame(row, bg=BG2)
            inner.pack(fill="x", padx=8, pady=5)

            dot = tk.Canvas(inner, bg=BG2, width=10, height=10, bd=0,
                            highlightthickness=0)
            dot.create_oval(1, 1, 9, 9, fill=SEG_COLS[i % len(SEG_COLS)], outline="")
            dot.pack(side="left", padx=(0, 6))

            name = os.path.basename(vp)
            tk.Label(inner, text=name, bg=BG2, fg=TEXT,
                     font=("Helvetica", 10), anchor="w").pack(side="left", fill="x", expand=True)

            # Preview button
            tk.Button(inner, text="▶", bg=BG2, fg=ACCENT, relief="flat",
                      bd=0, cursor="hand2", font=("Helvetica", 11),
                      command=lambda p=vp: self._switch_preview(p)
                      ).pack(side="right", padx=(4, 0))

            # Remove button
            tk.Button(inner, text="✕", bg=BG2, fg=TEXT3, relief="flat",
                      bd=0, cursor="hand2", font=("Helvetica", 11),
                      command=lambda idx=i: self._remove_video(idx)
                      ).pack(side="right")

    def _switch_preview(self, path: str):
        """Switch preview to a specific video in the list."""
        try:
            self._stop_evt.set()
            if self.clip:
                self.clip.close()
            self.clip       = VideoFileClip(path, audio=False)
            self.video_path = path
            self.duration   = self.clip.duration
            self.current_t  = 0.0
            self.is_playing = False
            self.play_btn.configure(text="▶  Play")
            self.out_var.set(fmt(self.duration))
            name = os.path.basename(path)
            self.file_lbl.configure(text=f"{name}  ·  {fmt(self.duration)}")
            self.file_badge.configure(text=name)
            for i, lbl in enumerate(self._tick_lbls):
                lbl.configure(text=fmt(self.duration * i / 4) if i < 4 else fmt(self.duration))
            self._stop_evt.clear()
            self._draw_scrub_track(0.0)
            self._show_frame(0.0)
            self.status_lbl.configure(text=f"Previewing: {name}")
        except Exception as e:
            messagebox.showerror("Load error", str(e))

    def _remove_video(self, idx: int):
        removed = self.video_paths.pop(idx)
        if self.video_path == removed and self.video_paths:
            self._switch_preview(self.video_paths[0])
        elif not self.video_paths:
            self.clear_videos()
        self._refresh_video_list()

    # ── Text overlay card ─────────────────────────────────────────────────────
    def _build_text_card(self, parent):
        outer, body = self._card(parent, "Text Overlay")
        outer.pack(fill="x", pady=(0, 10))

        self._label(body, "Overlay text", anchor="w")
        self.overlay_text = tk.StringVar(value="My Edited Video")
        tk.Entry(body, textvariable=self.overlay_text,
                 bg=BG2, fg=TEXT, insertbackground=TEXT,
                 font=("Helvetica", 12), bd=0,
                 highlightthickness=1, highlightbackground=BORDER,
                 relief="flat").pack(fill="x", pady=(3, 10), ipady=6)

        r = tk.Frame(body, bg=BG)
        r.pack(fill="x", pady=(0, 10))
        for col, (lbl, attr, val) in enumerate([
            ("Font size", "txt_size",  "50"),
            ("Show from", "txt_from",  "00:00.000"),
            ("Show until","txt_until", "end"),
        ]):
            r.columnconfigure(col, weight=1)
            tk.Label(r, text=lbl, bg=BG, fg=TEXT2, font=("Helvetica", 10)
                     ).grid(row=0, column=col, sticky="w", padx=(0 if col == 0 else 8, 0))
            v = tk.StringVar(value=val)
            setattr(self, attr, v)
            tk.Entry(r, textvariable=v, bg=BG2, fg=TEXT, insertbackground=TEXT,
                     font=("Helvetica", 11), bd=0,
                     highlightthickness=1, highlightbackground=BORDER,
                     relief="flat").grid(row=1, column=col, sticky="ew",
                                         padx=(0 if col == 0 else 8, 0), pady=3, ipady=5)

        cpo = tk.Frame(body, bg=BG)
        cpo.pack(fill="x")

        swf = tk.Frame(cpo, bg=BG)
        swf.pack(side="left")
        self._label(swf, "Colour", anchor="w")
        sf = tk.Frame(swf, bg=BG)
        sf.pack(pady=(4, 0))
        self._swatch_btns: list[tuple] = []
        for c in SWATCH_COLORS:
            b = tk.Button(sf, bg=c, width=2, height=1, cursor="hand2",
                          relief="solid", bd=1,
                          command=lambda col=c: self._pick_text_color(col))
            b.pack(side="left", padx=2)
            self._swatch_btns.append((b, c))
        self._pick_text_color("#ffffff")

        pf = tk.Frame(cpo, bg=BG)
        pf.pack(side="left", padx=20)
        self._label(pf, "Position", anchor="w")
        pg = tk.Frame(pf, bg=BG)
        pg.pack(pady=(4, 0))
        self._txt_pos_btns = self._pos_grid(pg, self.text_pos_idx, self._set_txt_pos)

        of = tk.Frame(cpo, bg=BG)
        of.pack(side="left", padx=10, fill="x", expand=True)
        self._label(of, "BG opacity", anchor="w")
        self.txt_opacity = tk.IntVar(value=60)
        ttk.Scale(of, from_=0, to=100, orient="horizontal",
                  variable=self.txt_opacity).pack(fill="x", pady=(4, 2))
        tk.Label(of, textvariable=self.txt_opacity, bg=BG, fg=TEXT,
                 font=("Courier", 10)).pack(anchor="w")

    # ── Logo overlay card ─────────────────────────────────────────────────────
    def _build_logo_card(self, parent):
        outer, body = self._card(parent, "Logo Overlay")
        outer.pack(fill="x", pady=(0, 10))

        top = tk.Frame(body, bg=BG)
        top.pack(fill="x", pady=(0, 12))

        self.logo_cv = tk.Canvas(top, bg=BG2, width=84, height=54,
                                  bd=0, highlightthickness=1, highlightbackground=BORDER)
        self.logo_cv.pack(side="left")
        self.logo_cv.create_text(42, 27, text="No logo", fill=TEXT3,
                                  font=("Helvetica", 9), tags="placeholder")

        info = tk.Frame(top, bg=BG)
        info.pack(side="left", padx=12, fill="x", expand=True)
        self.logo_name_lbl = tk.Label(info, text="No logo uploaded",
                                       bg=BG, fg=TEXT2, font=("Helvetica", 11))
        self.logo_name_lbl.pack(anchor="w")
        self._outline_btn(info, "Upload Logo", self.upload_logo, anchor="w", pady=(6, 0))

        ctrl = tk.Frame(body, bg=BG)
        ctrl.pack(fill="x")

        lpf = tk.Frame(ctrl, bg=BG)
        lpf.pack(side="left")
        self._label(lpf, "Screen position", anchor="w")
        lpg = tk.Frame(lpf, bg=BG)
        lpg.pack(pady=(4, 0))
        self._logo_pos_btns = self._pos_grid(lpg, self.logo_pos_idx, self._set_logo_pos)

        lr = tk.Frame(ctrl, bg=BG)
        lr.pack(side="left", padx=16, fill="x", expand=True)
        self._label(lr, "Opacity", anchor="w")
        self.logo_opacity = tk.IntVar(value=80)
        ttk.Scale(lr, from_=0, to=100, orient="horizontal",
                  variable=self.logo_opacity).pack(fill="x", pady=(3, 2))
        tk.Label(lr, textvariable=self.logo_opacity, bg=BG, fg=TEXT,
                 font=("Courier", 10)).pack(anchor="w", pady=(0, 8))

        tr = tk.Frame(lr, bg=BG)
        tr.pack(fill="x")
        tr.columnconfigure(0, weight=1)
        tr.columnconfigure(1, weight=1)
        for col, (lbl, attr, val) in enumerate([
            ("Show from",  "logo_from",  "00:00.000"),
            ("Show until", "logo_until", "end"),
        ]):
            tk.Label(tr, text=lbl, bg=BG, fg=TEXT2, font=("Helvetica", 10)
                     ).grid(row=0, column=col, sticky="w", padx=(0 if col == 0 else 8, 0))
            v = tk.StringVar(value=val)
            setattr(self, attr, v)
            tk.Entry(tr, textvariable=v, bg=BG2, fg=TEXT, insertbackground=TEXT,
                     font=("Courier", 11), bd=0,
                     highlightthickness=1, highlightbackground=BORDER,
                     relief="flat").grid(row=1, column=col, sticky="ew",
                                         padx=(0 if col == 0 else 8, 0), pady=3, ipady=4)

    # ── Audio files card ──────────────────────────────────────────────────────
    def _build_audio_card(self, parent):
        def _hdr_right(hdr):
            self.audio_count_lbl = tk.Label(hdr, text="0 files", bg=BG, fg=TEXT3,
                                             font=("Helvetica", 10))
            self.audio_count_lbl.pack(side="right")

        outer, body = self._card(parent, "Audio Files", _hdr_right)
        outer.pack(fill="x", pady=(0, 10))

        self.audio_list_body = body
        self._audio_list_rows: list[tk.Widget] = []

        # Mode selector (replace / mix)
        mode_row = tk.Frame(body, bg=BG)
        mode_row.pack(fill="x", pady=(0, 8))
        tk.Label(mode_row, text="On export:", bg=BG, fg=TEXT2,
                 font=("Helvetica", 10)).pack(side="left", padx=(0, 8))
        for val, label in [("replace", "Replace video audio"),
                            ("mix",     "Mix with video audio")]:
            tk.Radiobutton(mode_row, text=label, variable=self.audio_mode_var,
                           value=val, bg=BG, fg=TEXT, selectcolor=BG,
                           activebackground=BG, font=("Helvetica", 10),
                           cursor="hand2").pack(side="left", padx=(0, 10))

        # Upload button row
        btn_row = tk.Frame(body, bg=BG)
        btn_row.pack(fill="x", pady=(0, 8))
        self._accent_btn(btn_row, "+ Add Audio File(s)", self.upload_audio,
                         bg=GREEN, side="left")
        self._outline_btn(btn_row, "Clear Audio", self.clear_audio,
                          side="left", padx=(8, 0))

        # List area
        self.audio_files_frame = tk.Frame(body, bg=BG)
        self.audio_files_frame.pack(fill="x")
        self._refresh_audio_list()

    def _refresh_audio_list(self):
        for w in self._audio_list_rows:
            w.destroy()
        self._audio_list_rows.clear()

        count = len(self.audio_paths)
        if hasattr(self, "audio_count_lbl"):
            self.audio_count_lbl.configure(
                text=f"{count} file{'s' if count != 1 else ''}")

        if not self.audio_paths:
            lbl = tk.Label(self.audio_files_frame,
                           text="No audio loaded — click '+ Add Audio File(s)' above",
                           bg=BG, fg=TEXT3, font=("Helvetica", 10))
            lbl.pack(anchor="w", pady=4)
            self._audio_list_rows.append(lbl)
            return

        for i, ap in enumerate(self.audio_paths):
            row = tk.Frame(self.audio_files_frame, bg=BG2,
                           highlightthickness=1, highlightbackground=BORDER)
            row.pack(fill="x", pady=(0, 4))
            self._audio_list_rows.append(row)

            inner = tk.Frame(row, bg=BG2)
            inner.pack(fill="x", padx=8, pady=5)

            # Musical note icon in accent green
            tk.Label(inner, text="♪", bg=BG2, fg=GREEN,
                     font=("Helvetica", 12)).pack(side="left", padx=(0, 6))

            name = os.path.basename(ap)
            tk.Label(inner, text=name, bg=BG2, fg=TEXT,
                     font=("Helvetica", 10), anchor="w"
                     ).pack(side="left", fill="x", expand=True)

            # Remove button
            tk.Button(inner, text="✕", bg=BG2, fg=TEXT3, relief="flat",
                      bd=0, cursor="hand2", font=("Helvetica", 11),
                      activebackground=BG2,
                      command=lambda idx=i: self._remove_audio(idx)
                      ).pack(side="right")

    def _remove_audio(self, idx: int):
        self.audio_paths.pop(idx)
        self._refresh_audio_list()
        n = len(self.audio_paths)
        self.status_lbl.configure(
            text=f"{n} audio file{'s' if n != 1 else ''} loaded." if n else "Audio cleared.")

    def upload_audio(self):
        paths = filedialog.askopenfilenames(
            title="Select Audio File(s)",
            filetypes=[("Audio files",
                        "*.mp3 *.wav *.aac *.ogg *.flac *.m4a *.wma *.opus"),
                       ("All files", "*.*")])
        if not paths:
            return
        added = 0
        for p in paths:
            if p not in self.audio_paths:
                self.audio_paths.append(p)
                added += 1
        self._refresh_audio_list()
        n = len(self.audio_paths)
        self.status_lbl.configure(
            text=f"Added {added} audio file{'s' if added != 1 else ''}. "
                 f"{n} total loaded.")

    def clear_audio(self):
        self.audio_paths.clear()
        self._refresh_audio_list()
        if hasattr(self, "status_lbl"):
            self.status_lbl.configure(text="Audio cleared.")

    # ── Right column ──────────────────────────────────────────────────────────
    def _build_right(self):
        self._build_seg_card(self.right_f)
        self._build_export_card(self.right_f)

    def _build_seg_card(self, parent):
        def _hdr_right(hdr):
            self.seg_count_lbl = tk.Label(hdr, text="0 / 5", bg=BG, fg=TEXT3,
                                           font=("Helvetica", 10))
            self.seg_count_lbl.pack(side="right")
        outer, body = self._card(parent, "Segments", _hdr_right)
        outer.pack(fill="x", pady=(0, 10))
        self.seg_body = body
        self._seg_rows: list[tk.Widget] = []
        self._refresh_segs()

    def _build_export_card(self, parent):
        outer, body = self._card(parent, "Export As")
        outer.pack(fill="x", pady=(0, 10))

        for val, label in [
            ("merged",          "🎬  Merged single file"),
            ("separate",        "🗂️  Separate clips"),
            ("no_audio",        "🔇  No audio"),
            ("audio_only",      "🎵  Audio only (.mp3)"),
            ("mobile_crop",     "📱  Mobile crop 9:16 (merged)"),
            ("mobile_separate", "📱  Mobile crop 9:16 (separate)"),
        ]:
            tk.Radiobutton(body, text=label, variable=self.export_var, value=val,
                           bg=BG, fg=TEXT, selectcolor=BG, activebackground=BG,
                           font=("Helvetica", 11), cursor="hand2",
                           wraplength=260, justify="left"
                           ).pack(anchor="w", pady=2)

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=10)

        self.export_btn = tk.Button(
            body, text="Export Video", command=self.export_video,
            bg=ACCENT, fg="white", activebackground="#0C447C",
            activeforeground="white", relief="flat", bd=0, cursor="hand2",
            font=("Helvetica", 12, "bold"), padx=14, pady=9)
        self.export_btn.pack(fill="x")

        self.status_lbl = tk.Label(body, text="", bg=BG, fg=TEXT2,
                                    font=("Helvetica", 10),
                                    wraplength=270, justify="left")
        self.status_lbl.pack(anchor="w", pady=(8, 0))

    # ── Segment list refresh ──────────────────────────────────────────────────
    def _refresh_segs(self):
        for w in self._seg_rows:
            w.destroy()
        self._seg_rows.clear()

        for i, seg in enumerate(self.segments):
            color = SEG_COLS[i % len(SEG_COLS)]
            card  = tk.Frame(self.seg_body, bg=BG2,
                             highlightthickness=1, highlightbackground=BORDER)
            card.pack(fill="x", pady=(0, 6))
            self._seg_rows.append(card)

            inner = tk.Frame(card, bg=BG2)
            inner.pack(fill="x", padx=10, pady=8)

            top_row = tk.Frame(inner, bg=BG2)
            top_row.pack(fill="x")

            dot = tk.Canvas(top_row, bg=BG2, width=10, height=10,
                            bd=0, highlightthickness=0)
            dot.create_oval(1, 1, 9, 9, fill=color, outline="")
            dot.pack(side="left", padx=(0, 6))

            # Show source file name for multi-video segments
            src = seg.get("source", "")
            label = f"Clip {i + 1}"
            if src:
                label += f"  [{os.path.basename(src)}]"
            tk.Label(top_row, text=label, bg=BG2, fg=TEXT,
                     font=("Helvetica", 10, "bold")).pack(side="left")

            tk.Button(top_row, text="✕", bg=BG2, fg=TEXT3,
                      relief="flat", bd=0, cursor="hand2", activebackground=BG2,
                      font=("Helvetica", 12),
                      command=lambda x=i: self._del_seg(x)
                      ).pack(side="right")

            bb = tk.Frame(inner, bg=BG3, height=3)
            bb.pack(fill="x", pady=3)
            pct = (seg["out"] - seg["in"]) / self.duration if self.duration > 0 else 0.5
            tk.Frame(bb, bg=color, height=3).place(
                relwidth=min(1.0, max(0.05, pct)), relheight=1.0)

            tr = tk.Frame(inner, bg=BG2)
            tr.pack(fill="x")
            tk.Label(tr, text=fmt(seg["in"]),  bg=BG2, fg=TEXT2,
                     font=("Courier", 10)).pack(side="left")
            tk.Label(tr, text=fmt(seg["out"]), bg=BG2, fg=TEXT2,
                     font=("Courier", 10)).pack(side="right")

        for j in range(5 - len(self.segments)):
            slot = tk.Frame(self.seg_body, bg=BG,
                            highlightthickness=1, highlightbackground=BORDER)
            slot.pack(fill="x", pady=(0, 6))
            tk.Label(slot, text=f"+ Slot {len(self.segments) + j + 1}",
                     bg=BG, fg=TEXT3, font=("Helvetica", 11)).pack(pady=9)
            self._seg_rows.append(slot)

        self.seg_count_lbl.configure(text=f"{len(self.segments)} / 5")
        # Redraw scrub track to update segment markers
        self._draw_scrub_track(self.current_t)

    def _del_seg(self, idx: int):
        del self.segments[idx]
        self._refresh_segs()

    # ── Overlay pickers ───────────────────────────────────────────────────────
    def _pick_text_color(self, color):
        self.text_color = color
        for btn, c in self._swatch_btns:
            btn.configure(bd=3 if c == color else 1,
                          relief="solid" if c == color else "flat")

    def _set_txt_pos(self, idx):
        self.text_pos_idx = idx
        for i, b in enumerate(self._txt_pos_btns):
            b.configure(bg=ACCENT if i == idx else BG3)

    def _set_logo_pos(self, idx):
        self.logo_pos_idx = idx
        for i, b in enumerate(self._logo_pos_btns):
            b.configure(bg=ACCENT if i == idx else BG3)

    # ── Video upload ──────────────────────────────────────────────────────────
    def upload_video(self):
        paths = filedialog.askopenfilenames(
            title="Select Video(s)",
            filetypes=[("Video files",
                        "*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm"),
                       ("All files", "*.*")])
        if not paths:
            return
        for p in paths:
            if p not in self.video_paths:
                self.video_paths.append(p)
        self._refresh_video_list()
        # Auto-preview first newly added video
        self._switch_preview(paths[0])
        n = len(self.video_paths)
        self.file_lbl.configure(text=f"{n} video{'s' if n > 1 else ''} loaded")

    def clear_videos(self):
        self._stop_evt.set()
        if self.clip:
            try:
                self.clip.close()
            except Exception:
                pass
            self.clip = None
        self.video_paths.clear()
        self.video_path = ""
        self.duration   = 0.0
        self.current_t  = 0.0
        self.is_playing = False
        self.play_btn.configure(text="▶  Play")
        self.file_lbl.configure(text="No video loaded")
        self.file_badge.configure(text="")
        self.pv.delete("all")
        self.pv.create_text(320, 140, text="Upload a video to preview",
                             fill="#666699", font=("Helvetica", 12))
        self._draw_scrub_track(0.0)
        for lbl in self._tick_lbls:
            lbl.configure(text="")
        self._refresh_video_list()

    def upload_logo(self):
        path = filedialog.askopenfilename(
            title="Select Logo (PNG recommended)",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp *.gif"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            self.logo_path = path
            img = Image.open(path).convert("RGBA")
            img.thumbnail((80, 50), Image.LANCZOS)
            self._logo_thumb = ImageTk.PhotoImage(img)
            self.logo_cv.delete("all")
            self.logo_cv.create_image(42, 27, image=self._logo_thumb, anchor="center")
            self.logo_name_lbl.configure(text=os.path.basename(path))
            if self.clip:
                self._show_frame(self.current_t)
        except Exception as e:
            messagebox.showerror("Logo error", str(e))

    # ── Preview rendering ─────────────────────────────────────────────────────
    def _show_frame(self, t: float):
        if not self.clip:
            return
        try:
            t = max(0.0, min(t, self.duration - 0.001))
            frame = self.clip.get_frame(t)
            img   = Image.fromarray(frame)

            cw = self.pv.winfo_width()
            ch = self.pv.winfo_height()
            if cw <= 1: cw = 640
            if ch <= 1: ch = 280
            vw, vh = self.clip.size
            ratio = min(cw / vw, ch / vh)
            nw, nh = int(vw * ratio), int(vh * ratio)
            img = img.resize((nw, nh), Image.LANCZOS)
            img = self._draw_preview_overlays(img, nw, nh)

            self._pv_photo = ImageTk.PhotoImage(img)
            self.pv.delete("all")
            self.pv.create_image(cw // 2, ch // 2,
                                  image=self._pv_photo, anchor="center")
            self.pv.create_text(cw - 8, ch - 8, text=fmt(t),
                                 fill="#aaaaaa", font=("Courier", 9), anchor="se")
        except Exception:
            pass

    def _draw_preview_overlays(self, img, w, h):
        img = img.convert("RGBA")
        ov  = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        drw = ImageDraw.Draw(ov)

        txt = self.overlay_text.get().strip()
        if txt:
            try:
                raw_size = int(self.txt_size.get() or 50)
            except ValueError:
                raw_size = 50
            fs   = max(8, int(raw_size * w // 720))
            font = load_font(fs)
            try:
                bb = drw.textbbox((0, 0), txt, font=font)
                tw, th = bb[2] - bb[0], bb[3] - bb[1]
            except Exception:
                tw, th = len(txt) * fs // 2, fs
            px, py = overlay_xy(self.text_pos_idx, w, h, tw, th, 10)
            op = int(self.txt_opacity.get() / 100 * 255)
            drw.rectangle([px - 6, py - 3, px + tw + 6, py + th + 3],
                           fill=(0, 0, 0, op))
            c = self.text_color
            try:
                r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
            except Exception:
                r, g, b = 255, 255, 255
            drw.text((px, py), txt, font=font, fill=(r, g, b, 255))

        if self.logo_path and os.path.exists(self.logo_path):
            try:
                logo = Image.open(self.logo_path).convert("RGBA")
                max_lw = max(40, int(w * 0.14))
                if logo.width > max_lw:
                    logo = logo.resize(
                        (max_lw, int(logo.height * max_lw / logo.width)),
                        Image.LANCZOS)
                op  = int(self.logo_opacity.get() / 100 * 255)
                r2, g2, b2, a2 = logo.split()
                a2  = a2.point(lambda p: int(p * op / 255))
                logo = Image.merge("RGBA", (r2, g2, b2, a2))
                lx, ly = overlay_xy(self.logo_pos_idx, w, h,
                                    logo.width, logo.height, 8)
                ov.paste(logo, (lx, ly), logo)
            except Exception:
                pass

        return Image.alpha_composite(img, ov).convert("RGB")

    # ── Scrubber & playback ───────────────────────────────────────────────────
    def _on_scrub(self, val):
        if not self.clip:
            return
        t = float(val)
        self.current_t = t
        self.timecode_lbl.configure(text=f"{fmt(t)} / {fmt(self.duration)}")
        self._draw_scrub_track(t)
        self._show_frame(t)

    def mark_in(self):
        self.in_var.set(fmt(self.current_t))
        self._recalc_dur()

    def mark_out(self):
        self.out_var.set(fmt(self.current_t))
        self._recalc_dur()

    def _recalc_dur(self, *_):
        t_in  = parse_time(self.in_var.get())
        t_out = parse_time(self.out_var.get())
        if t_in is not None and t_out is not None and t_out > t_in:
            self.dur_var.set(f"Duration: {t_out - t_in:.1f}s")
        else:
            self.dur_var.set("Duration: —")

    def toggle_play(self):
        if not self.clip:
            return
        if self.is_playing:
            self._stop_evt.set()
        else:
            self._stop_evt.clear()
            self.is_playing = True
            self.play_btn.configure(text="⏸  Pause")
            threading.Thread(target=self._play_loop, daemon=True).start()

    def _play_loop(self):
        fps = min(float(self.clip.fps or 24), 30)
        dt  = 1.0 / fps
        t   = self.current_t

        # ── Optional pygame audio ──────────────────────────────────────────
        _pygame_ok = False
        _audio_clip = None
        try:
            import pygame
            tmp_wav = os.path.join(tempfile.gettempdir(), "_mve_preview.wav")
            _audio_clip = VideoFileClip(self.video_path)
            if _audio_clip.audio is not None:
                _audio_clip.audio.write_audiofile(tmp_wav, logger=None, fps=44100)
                pygame.mixer.init(frequency=44100)
                pygame.mixer.music.load(tmp_wav)
                pygame.mixer.music.play(start=t)
                _pygame_ok = True
        except Exception:
            pass

        while not self._stop_evt.is_set():
            tick = time.perf_counter()
            if t >= self.duration:
                t = 0.0
                if _pygame_ok:
                    try:
                        import pygame
                        pygame.mixer.music.rewind()
                        pygame.mixer.music.play()
                    except Exception:
                        pass
            self.current_t = t
            _t = t
            self.root.after(0, self._show_frame, _t)
            self.root.after(0, lambda v=_t: self._draw_scrub_track(v))
            self.root.after(0, lambda v=_t: self.timecode_lbl.configure(
                text=f"{fmt(v)} / {fmt(self.duration)}"))
            elapsed = time.perf_counter() - tick
            time.sleep(max(0.0, dt - elapsed))
            t += dt

        if _pygame_ok:
            try:
                import pygame
                pygame.mixer.music.stop()
                pygame.mixer.quit()
            except Exception:
                pass
        if _audio_clip:
            try:
                _audio_clip.close()
            except Exception:
                pass

        self.is_playing = False
        self.root.after(0, lambda: self.play_btn.configure(text="▶  Play"))

    # ── Segment management ────────────────────────────────────────────────────
    def add_segment(self):
        if not self.clip:
            messagebox.showwarning("No video", "Upload a video first.")
            return
        if len(self.segments) >= 5:
            messagebox.showwarning("Limit reached", "Maximum 5 segments.")
            return
        t_in  = parse_time(self.in_var.get())
        t_out = parse_time(self.out_var.get())
        if t_in is None or t_out is None or t_out <= t_in:
            messagebox.showwarning("Invalid range",
                                   "Set valid In and Out points first.")
            return
        self.segments.append({
            "in":     max(0.0, t_in),
            "out":    min(t_out, self.duration),
            "source": self.video_path,   # track which file this came from
        })
        self._refresh_segs()
        self.status_lbl.configure(text=f"Segment {len(self.segments)} added.")

    # ── Export ────────────────────────────────────────────────────────────────
    def export_video(self):
        if not self.video_paths:
            messagebox.showwarning("No video", "Upload a video first.")
            return
        if not self.segments:
            messagebox.showwarning("No segments", "Add at least one segment first.")
            return

        opt = self.export_var.get()
        if opt == "audio_only":
            path = filedialog.asksaveasfilename(
                defaultextension=".mp3",
                filetypes=[("MP3 audio", "*.mp3")],
                initialfile="output_audio.mp3")
        elif opt in ("separate", "mobile_separate"):
            path = filedialog.askdirectory(title="Choose output folder")
        else:
            path = filedialog.asksaveasfilename(
                defaultextension=".mp4",
                filetypes=[("MP4 video", "*.mp4")],
                initialfile="output.mp4")
        if not path:
            return

        self.export_btn.configure(state="disabled", text="Exporting…")
        self.status_lbl.configure(text="Processing — please wait…")
        self.root.update()
        threading.Thread(target=self._do_export, args=(opt, path), daemon=True).start()

    def _do_export(self, opt: str, path: str):
        tmp_dir   = tempfile.gettempdir()
        orig_dir  = os.getcwd()
        os.chdir(tmp_dir)
        tmp_audio = os.path.join(tmp_dir, "mve_temp_audio.mp4")

        write_kw = dict(
            codec="libx264",
            audio_codec="aac",
            logger=None,
            temp_audiofile=tmp_audio,
            ffmpeg_params=_FFMPEG_COMPAT,
        )

        export_clips: list[VideoFileClip] = []
        audio_clips:  list              = []
        try:
            # Build subclips — each segment may come from a different source file
            sub = []
            for seg in self.segments:
                src = seg.get("source") or (self.video_paths[0] if self.video_paths else self.video_path)
                ec  = VideoFileClip(src)
                export_clips.append(ec)
                sub.append(ec.subclipped(seg["in"], min(seg["out"], ec.duration)))

            # ── Helper: apply uploaded audio to a clip ────────────────────
            def _apply_audio(clip):
                """Replace or mix the clip's audio with any uploaded audio files."""
                if not self.audio_paths:
                    return clip
                # Load and concatenate uploaded audio tracks
                loaded = []
                for ap in self.audio_paths:
                    try:
                        ac = AudioFileClip(ap)
                        audio_clips.append(ac)
                        loaded.append(ac)
                    except Exception as e:
                        print(f"[audio load] {ap}: {e}")
                if not loaded:
                    return clip
                # Concatenate the audio tracks in order, then trim/loop to match clip
                combined = concatenate_audioclips(loaded)
                # Trim to video duration, or loop if shorter
                if combined.duration < clip.duration:
                    repeats = int(clip.duration / combined.duration) + 1
                    combined = concatenate_audioclips([combined] * repeats)
                combined = combined.subclipped(0, clip.duration)
                if self.audio_mode_var.get() == "mix" and clip.audio is not None:
                    mixed = CompositeAudioClip([clip.audio, combined])
                    return clip.with_audio(mixed)
                else:
                    return clip.with_audio(combined)

            # ── Audio only ────────────────────────────────────────────────
            if opt == "audio_only":
                merged = concatenate_videoclips(sub)
                merged = _apply_audio(merged)
                if merged.audio is None:
                    raise RuntimeError("This video has no audio track.")
                merged.audio.write_audiofile(path, logger=None)
                self.root.after(0, self._export_done,
                                f"Audio saved:\n{os.path.basename(path)}")
                return

            # ── Separate clips ─────────────────────────────────────────────
            if opt == "separate":
                for i, c in enumerate(sub):
                    c = _apply_audio(c)
                    c = self._apply_overlays(c)
                    out = os.path.join(path, f"clip_{i + 1:02d}.mp4")
                    c.write_videofile(out, **write_kw)
                self.root.after(0, self._export_done,
                                f"{len(sub)} clips saved to:\n{path}")
                return

            # ── Mobile crop separate ───────────────────────────────────────
            if opt == "mobile_separate":
                for i, c in enumerate(sub):
                    c = _do_mobile_crop(c)
                    c = _apply_audio(c)
                    c = self._apply_overlays(c)
                    out = os.path.join(path, f"clip_{i + 1:02d}_9x16.mp4")
                    c.write_videofile(out, **write_kw)
                self.root.after(0, self._export_done,
                                f"{len(sub)} 9:16 clips saved to:\n{path}")
                return

            # ── Merged ────────────────────────────────────────────────────
            final = concatenate_videoclips(sub)
            final = _apply_audio(final)

            if opt == "no_audio":
                final = final.without_audio()
                kw = {k: v for k, v in write_kw.items()
                      if k not in ("audio_codec", "temp_audiofile")}
            else:
                kw = write_kw

            if opt == "mobile_crop":
                final = _do_mobile_crop(final)

            final = self._apply_overlays(final)
            final.write_videofile(path, **kw)
            self.root.after(0, self._export_done,
                            f"Saved:\n{os.path.basename(path)}")

        except Exception as exc:
            self.root.after(0, self._export_error, str(exc))
        finally:
            os.chdir(orig_dir)
            for ec in export_clips:
                try:
                    ec.close()
                except Exception:
                    pass
            for ac in audio_clips:
                try:
                    ac.close()
                except Exception:
                    pass
            try:
                if os.path.exists(tmp_audio):
                    os.remove(tmp_audio)
            except Exception:
                pass

    # ── Overlay composition ───────────────────────────────────────────────────
    def _apply_overlays(self, clip):
        layers = [clip]
        fw, fh = clip.size

        txt = self.overlay_text.get().strip()
        if txt:
            try:
                try:
                    fs = max(12, int(self.txt_size.get() or 50))
                except ValueError:
                    fs = 50
                font = load_font(fs)
                measure_img = Image.new("RGBA", (1, 1))
                md = ImageDraw.Draw(measure_img)
                try:
                    bb = md.textbbox((0, 0), txt, font=font)
                    tw, th = bb[2] - bb[0], bb[3] - bb[1]
                except Exception:
                    tw, th = len(txt) * fs // 2, fs

                canvas = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
                draw   = ImageDraw.Draw(canvas)
                px, py = overlay_xy(self.text_pos_idx, fw, fh, tw, th, 20)
                op = int(self.txt_opacity.get() / 100 * 255)
                draw.rectangle([px - 10, py - 5, px + tw + 10, py + th + 5],
                               fill=(0, 0, 0, op))
                c = self.text_color
                r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
                draw.text((px, py), txt, font=font, fill=(r, g, b, 255))

                tc = ImageClip(np.array(canvas))
                t0 = parse_time(self.txt_from.get())  or 0.0
                t1 = parse_time(self.txt_until.get()) or clip.duration
                t1 = min(t1, clip.duration)
                tc = tc.with_start(t0).with_end(t1)
                layers.append(tc)
            except Exception as e:
                print(f"[text overlay] {e}")

        if self.logo_path and os.path.exists(self.logo_path):
            try:
                logo = Image.open(self.logo_path).convert("RGBA")
                max_lw = max(60, int(fw * 0.12))
                if logo.width > max_lw:
                    logo = logo.resize(
                        (max_lw, int(logo.height * max_lw / logo.width)),
                        Image.LANCZOS)
                op  = int(self.logo_opacity.get() / 100 * 255)
                r2, g2, b2, a2 = logo.split()
                a2  = a2.point(lambda p: int(p * op / 255))
                logo = Image.merge("RGBA", (r2, g2, b2, a2))
                lx, ly = overlay_xy(self.logo_pos_idx, fw, fh,
                                    logo.width, logo.height, 16)
                canvas = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
                canvas.paste(logo, (lx, ly), logo)
                lc = ImageClip(np.array(canvas))
                t0 = parse_time(self.logo_from.get())  or 0.0
                t1 = parse_time(self.logo_until.get()) or clip.duration
                t1 = min(t1, clip.duration)
                lc = lc.with_start(t0).with_end(t1)
                layers.append(lc)
            except Exception as e:
                print(f"[logo overlay] {e}")

        return CompositeVideoClip(layers) if len(layers) > 1 else clip

    # ── Export callbacks ──────────────────────────────────────────────────────
    def _export_done(self, msg: str):
        self.export_btn.configure(state="normal", text="Export Video")
        self.status_lbl.configure(text=msg)
        messagebox.showinfo("Export complete", msg)

    def _export_error(self, msg: str):
        self.export_btn.configure(state="normal", text="Export Video")
        self.status_lbl.configure(text=f"Error: {msg}")
        messagebox.showerror("Export failed", msg)

    # ── Shutdown ──────────────────────────────────────────────────────────────
    def on_close(self):
        self._stop_evt.set()
        if self.clip:
            try:
                self.clip.close()
            except Exception:
                pass
        self.root.destroy()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def main():
    root = tk.Tk()
    try:
        app = VideoEditor(root)
        root.protocol("WM_DELETE_WINDOW", app.on_close)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        messagebox.showerror("Startup error",
                             f"Failed to initialise:\n\n{exc}\n\nSee terminal.")
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
