from __future__ import annotations

import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import ytdlr_core as core


@dataclass
class _UiEvent:
    kind: str
    payload: object


def _split_args(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    try:
        return shlex.split(t)
    except Exception:
        return t.split()


class YtDlrGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("yt-dlr")
        self.minsize(900, 620)

        self._events: queue.Queue[_UiEvent] = queue.Queue()

        self._search_thread: threading.Thread | None = None
        self._search_proc: subprocess.Popen[str] | None = None
        self._info_thread: threading.Thread | None = None
        self._info_proc: subprocess.Popen[str] | None = None
        self._download_thread: threading.Thread | None = None
        self._download_proc: subprocess.Popen[str] | None = None
        self._preview_thread: threading.Thread | None = None
        self._preview_proc: subprocess.Popen[str] | None = None

        self._items: list[core.SearchItem] = []
        self._selected_url: str = ""
        self._formats: list[core.FormatItem] = []

        self._sort_search_col = "views"
        self._sort_search_desc = True
        self._sort_formats_col = "tbr"
        self._sort_formats_desc = True

        self.var_query = tk.StringVar(value="")
        self.var_limit = tk.IntVar(value=20)
        self.var_fast = tk.BooleanVar(value=True)

        self.var_out_dir = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.var_mode = tk.StringVar(value="av")
        self.var_single_file = tk.BooleanVar(value=False)
        self.var_no_playlist = tk.BooleanVar(value=True)
        self.var_merge = tk.StringVar(value="auto")
        self.var_template = tk.StringVar(value=core.DEFAULT_TEMPLATE)
        self.var_extra = tk.StringVar(value="")
        self.var_format = tk.StringVar(value="")
        self.var_prefer_recordbox = tk.BooleanVar(value=True)
        self.var_filter_mp4 = tk.BooleanVar(value=True)
        self.var_embed_preview = tk.BooleanVar(value=True)
        self.var_dj_dir = tk.StringVar(value=str(self._default_dj_dir()))
        self.var_status = tk.StringVar(value="")
        self.var_url = tk.StringVar(value="")

        self._build_ui()
        self.var_format.trace_add("write", lambda *_: self._sync_controls())
        self._sync_controls()
        self.after(80, self._pump_events)
        self.after(120, self._bring_to_front)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _bring_to_front(self) -> None:
        try:
            self.deiconify()
        except Exception:
            pass
        try:
            self.lift()
        except Exception:
            pass
        try:
            self.focus_force()
        except Exception:
            pass
        try:
            if self.tk.call("tk", "windowingsystem") == "aqua":
                self.attributes("-topmost", True)
                self.after(200, lambda: self.attributes("-topmost", False))
        except Exception:
            pass

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x")
        ttk.Label(top, text="Keresés:").pack(side="left")
        ent = ttk.Entry(top, textvariable=self.var_query)
        ent.pack(side="left", fill="x", expand=True, padx=(8, 8))
        ent.bind("<Return>", lambda _e: self._start_search())

        ttk.Label(top, text="Limit:").pack(side="left")
        ttk.Spinbox(top, from_=1, to=50, textvariable=self.var_limit, width=4).pack(side="left", padx=(6, 8))
        ttk.Checkbutton(top, text="Gyors (kevesebb adat)", variable=self.var_fast).pack(side="left")

        self.btn_search = ttk.Button(top, text="Search", command=self._start_search)
        self.btn_search.pack(side="left", padx=(8, 0))
        self.btn_search_cancel = ttk.Button(top, text="Stop", command=self._cancel_search)
        self.btn_search_cancel.pack(side="left", padx=(6, 0))

        mid = ttk.PanedWindow(root, orient="horizontal")
        mid.pack(fill="both", expand=True, pady=(10, 10))

        left = ttk.Frame(mid, padding=(0, 0, 10, 0))
        right = ttk.Frame(mid)
        mid.add(left, weight=3)
        mid.add(right, weight=2)

        ttk.Label(left, text="Találatok").pack(anchor="w")
        self.tree = ttk.Treeview(left, columns=("title", "uploader", "duration", "views"), show="headings", height=14)
        self.tree.heading("title", text="Cím", command=lambda: self._sort_search("title"))
        self.tree.heading("uploader", text="Csatorna", command=lambda: self._sort_search("uploader"))
        self.tree.heading("duration", text="Hossz", command=lambda: self._sort_search("duration"))
        self.tree.heading("views", text="Megtekintés", command=lambda: self._sort_search("views"))
        self.tree.column("title", width=420, anchor="w")
        self.tree.column("uploader", width=180, anchor="w")
        self.tree.column("duration", width=70, anchor="e")
        self.tree.column("views", width=90, anchor="e")
        ysb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self.tree.bind("<Double-1>", lambda _e: self._start_download())

        ttk.Label(right, text="Kiválasztott videó").pack(anchor="w")
        url_row = ttk.Frame(right)
        url_row.pack(fill="x", pady=(6, 0))
        ttk.Entry(url_row, textvariable=self.var_url, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(url_row, text="Open", width=8, command=self._open_url).pack(side="left", padx=(6, 0))
        ttk.Button(url_row, text="Copy", width=8, command=self._copy_url).pack(side="left", padx=(6, 0))
        ttk.Button(url_row, text="Preview", width=8, command=self._start_preview).pack(side="left", padx=(6, 0))
        self.btn_preview_stop = ttk.Button(url_row, text="Stop", width=7, command=self._stop_preview)
        self.btn_preview_stop.pack(side="left", padx=(6, 0))

        dj_box = ttk.LabelFrame(right, text="DJ Rush (Rekordbox)", padding=10)
        dj_box.pack(fill="x", pady=(10, 0))
        dj_row = ttk.Frame(dj_box)
        dj_row.pack(fill="x")
        ttk.Label(dj_row, text="Watch folder:").pack(side="left")
        ttk.Entry(dj_row, textvariable=self.var_dj_dir).pack(side="left", fill="x", expand=True, padx=(8, 6))
        ttk.Button(dj_row, text="Browse…", command=self._browse_dj_dir).pack(side="left")
        ttk.Button(dj_row, text="DJ Download (m4a)", command=self._dj_download, width=16).pack(side="left", padx=(8, 0))

        fmt_box = ttk.LabelFrame(right, text="Formátumok (letöltés előtt)", padding=10)
        fmt_box.pack(fill="both", expand=True, pady=(10, 0))

        fmt_top = ttk.Frame(fmt_box)
        fmt_top.pack(fill="x")
        self.btn_formats = ttk.Button(fmt_top, text="List formats", command=self._start_info)
        self.btn_formats.pack(side="left")
        self.btn_formats_cancel = ttk.Button(fmt_top, text="Stop", command=self._cancel_info)
        self.btn_formats_cancel.pack(side="left", padx=(6, 0))
        ttk.Checkbutton(fmt_top, text="Csak mp4/m4a", variable=self.var_filter_mp4, command=self._refresh_formats_tree).pack(
            side="left", padx=(12, 0)
        )
        ttk.Checkbutton(fmt_top, text="Embed preview (mpv)", variable=self.var_embed_preview).pack(side="left", padx=(12, 0))

        self.preview_host = tk.Frame(fmt_box, bg="black", height=200)
        self.preview_host.pack(fill="x", pady=(8, 0))
        self.preview_host.pack_propagate(False)

        self.tree_fmt = ttk.Treeview(
            fmt_box,
            columns=("id", "kind", "ext", "res", "fps", "vcodec", "acodec", "tbr", "size", "note"),
            show="headings",
            height=8,
            selectmode="extended",
        )
        for c, label, w, anchor, stretch in (
            ("id", "ID", 60, "w", False),
            ("kind", "Type", 50, "w", False),
            ("ext", "Ext", 50, "w", False),
            ("res", "Res", 90, "w", False),
            ("fps", "FPS", 50, "e", False),
            ("vcodec", "VCodec", 110, "w", True),
            ("acodec", "ACodec", 110, "w", True),
            ("tbr", "Bitrate", 70, "e", False),
            ("size", "Size", 80, "e", False),
            ("note", "Note", 120, "w", True),
        ):
            self.tree_fmt.heading(c, text=label, command=lambda col=c: self._sort_formats(col))
            self.tree_fmt.column(c, width=w, anchor=anchor, stretch=stretch)
        ysb2 = ttk.Scrollbar(fmt_box, orient="vertical", command=self.tree_fmt.yview)
        self.tree_fmt.configure(yscrollcommand=ysb2.set)
        self.tree_fmt.pack(side="left", fill="both", expand=True, pady=(8, 0))
        ysb2.pack(side="left", fill="y", pady=(8, 0))
        self.tree_fmt.bind("<Double-1>", lambda _e: self._use_selected_format())

        fmt_sel = ttk.Frame(fmt_box)
        fmt_sel.pack(fill="x", pady=(8, 0))
        ttk.Label(fmt_sel, text="-f:").pack(side="left")
        ttk.Entry(fmt_sel, textvariable=self.var_format).pack(side="left", fill="x", expand=True, padx=(8, 6))
        ttk.Button(fmt_sel, text="Use selected", command=self._use_selected_format).pack(side="left")
        ttk.Button(fmt_sel, text="Clear", command=lambda: self.var_format.set("")).pack(side="left", padx=(6, 0))

        opts = ttk.LabelFrame(right, text="Letöltés", padding=10)
        opts.pack(fill="x", pady=(10, 0))

        out_row = ttk.Frame(opts)
        out_row.pack(fill="x")
        ttk.Label(out_row, text="Mentés ide:").pack(side="left")
        ttk.Entry(out_row, textvariable=self.var_out_dir).pack(side="left", fill="x", expand=True, padx=(8, 6))
        ttk.Button(out_row, text="Browse…", command=self._browse_out).pack(side="left")

        mode_row = ttk.Frame(opts)
        mode_row.pack(fill="x", pady=(8, 0))
        ttk.Label(mode_row, text="Mode:").pack(side="left")
        ttk.Radiobutton(mode_row, text="Best A/V", value="av", variable=self.var_mode, command=self._sync_controls).pack(
            side="left", padx=(8, 0)
        )
        ttk.Radiobutton(mode_row, text="Best video", value="video", variable=self.var_mode, command=self._sync_controls).pack(
            side="left", padx=(8, 0)
        )
        ttk.Radiobutton(mode_row, text="Best audio", value="audio", variable=self.var_mode, command=self._sync_controls).pack(
            side="left", padx=(8, 0)
        )

        row2 = ttk.Frame(opts)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(row2, text="Csak egy fájl (nincs merge)", variable=self.var_single_file, command=self._sync_controls).pack(
            side="left"
        )
        ttk.Checkbutton(row2, text="No playlist", variable=self.var_no_playlist).pack(side="left", padx=(14, 0))

        merge_row = ttk.Frame(opts)
        merge_row.pack(fill="x", pady=(6, 0))
        ttk.Label(merge_row, text="Merge konténer:").pack(side="left")
        self.cmb_merge = ttk.Combobox(merge_row, textvariable=self.var_merge, values=("auto", "mp4", "mkv", "webm"), width=8, state="readonly")
        self.cmb_merge.pack(side="left", padx=(8, 0))

        pref_row = ttk.Frame(opts)
        pref_row.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(
            pref_row,
            text="Prefer Recordbox (mp4/h264+aac)",
            variable=self.var_prefer_recordbox,
            command=self._sync_controls,
        ).pack(side="left")

        tmpl_row = ttk.Frame(opts)
        tmpl_row.pack(fill="x", pady=(6, 0))
        ttk.Label(tmpl_row, text="Név sablon:").pack(side="left")
        ttk.Entry(tmpl_row, textvariable=self.var_template).pack(side="left", fill="x", expand=True, padx=(8, 0))

        extra_row = ttk.Frame(opts)
        extra_row.pack(fill="x", pady=(6, 0))
        ttk.Label(extra_row, text="Extra yt-dlp opciók:").pack(side="left")
        ttk.Entry(extra_row, textvariable=self.var_extra).pack(side="left", fill="x", expand=True, padx=(8, 0))

        btns = ttk.Frame(opts)
        btns.pack(fill="x", pady=(10, 0))
        self.btn_download = ttk.Button(btns, text="Download", command=self._start_download)
        self.btn_download.pack(side="left")
        self.btn_cancel = ttk.Button(btns, text="Cancel", command=self._cancel_download)
        self.btn_cancel.pack(side="left", padx=(8, 0))

        ttk.Label(right, textvariable=self.var_status).pack(anchor="w", pady=(10, 0))

        ttk.Label(root, text="Log").pack(anchor="w")
        self.log = ScrolledText(root, height=10)
        self.log.pack(fill="both", expand=False, pady=(6, 0))
        self.log.configure(state="disabled")

    def _append_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", (line.rstrip() + "\n"))
        self.log.see("end")
        self.log.configure(state="disabled")

    def _pump_events(self) -> None:
        try:
            while True:
                ev = self._events.get_nowait()
                if ev.kind == "log":
                    self._append_log(str(ev.payload))
                elif ev.kind == "status":
                    self.var_status.set(str(ev.payload))
                elif ev.kind == "results":
                    self._apply_results(ev.payload)
                elif ev.kind == "formats":
                    self._formats = [x for x in (ev.payload if isinstance(ev.payload, list) else []) if isinstance(x, core.FormatItem)]
                    self._refresh_formats_tree()
                elif ev.kind == "error":
                    messagebox.showerror("yt-dlr", str(ev.payload))
                elif ev.kind == "done":
                    self._sync_controls()
        except queue.Empty:
            pass
        self.after(80, self._pump_events)

    def _sync_controls(self) -> None:
        searching = self._search_thread is not None and self._search_thread.is_alive()
        infing = self._info_thread is not None and self._info_thread.is_alive()
        downloading = self._download_thread is not None and self._download_thread.is_alive()
        previewing = self._preview_thread is not None and self._preview_thread.is_alive()

        self.btn_search.configure(state=("disabled" if searching else "normal"))
        self.btn_search_cancel.configure(state=("normal" if searching else "disabled"))
        self.btn_formats.configure(state=("disabled" if infing else "normal"))
        self.btn_formats_cancel.configure(state=("normal" if infing else "disabled"))
        self.btn_download.configure(state=("disabled" if downloading else "normal"))
        self.btn_cancel.configure(state=("normal" if downloading else "disabled"))
        self.btn_preview_stop.configure(state=("normal" if previewing else "disabled"))

        mode = (self.var_mode.get() or "av").strip().lower()
        single = bool(self.var_single_file.get())
        manual_fmt = bool((self.var_format.get() or "").strip())
        merge_state = "readonly" if (mode == "av" and not single) else "disabled"
        if manual_fmt:
            merge_state = "readonly"
        self.cmb_merge.configure(state=merge_state)

    def _browse_out(self) -> None:
        p = filedialog.askdirectory(initialdir=self.var_out_dir.get() or str(Path.home()))
        if p:
            self.var_out_dir.set(p)

    def _browse_dj_dir(self) -> None:
        p = filedialog.askdirectory(initialdir=self.var_dj_dir.get() or str(Path.home()))
        if p:
            self.var_dj_dir.set(p)

    def _open_url(self) -> None:
        url = (self.var_url.get() or "").strip()
        if url:
            webbrowser.open(url)

    def _copy_url(self) -> None:
        url = (self.var_url.get() or "").strip()
        if not url:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(url)
            self.var_status.set("URL copied.")
        except Exception:
            pass

    def _apply_results(self, payload: object) -> None:
        items = payload if isinstance(payload, list) else []
        self._items = [x for x in items if isinstance(x, core.SearchItem)]
        self._rebuild_search_tree()
        self.var_status.set(f"Találatok: {len(self._items)}")
        self._clear_formats()
        self._stop_preview()

    def _on_select(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
        except Exception:
            return
        if idx < 0 or idx >= len(self._items):
            return
        url = self._items[idx].webpage_url
        self._selected_url = url
        self.var_url.set(url)
        self._clear_formats()
        self._stop_preview()

    def _search_sort_key(self, it: core.SearchItem):
        col = self._sort_search_col
        if col == "title":
            return (it.title or "").lower()
        if col == "uploader":
            return (it.uploader or "").lower()
        if col == "duration":
            return int(it.duration or -1)
        return int(it.view_count or -1)

    def _rebuild_search_tree(self) -> None:
        selected_url = (self.var_url.get() or self._selected_url or "").strip()
        try:
            self._items.sort(key=self._search_sort_key, reverse=bool(self._sort_search_desc))
        except Exception:
            pass

        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for idx, it in enumerate(self._items):
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    it.title,
                    it.uploader,
                    core.format_duration(it.duration),
                    core.format_views(it.view_count),
                ),
            )

        if selected_url:
            for idx, it in enumerate(self._items):
                if it.webpage_url == selected_url:
                    try:
                        self.tree.selection_set(str(idx))
                        self.tree.see(str(idx))
                    except Exception:
                        pass
                    break

    def _sort_search(self, col: str) -> None:
        if col == self._sort_search_col:
            self._sort_search_desc = not self._sort_search_desc
        else:
            self._sort_search_col = col
            self._sort_search_desc = col in ("views", "duration")
        self._rebuild_search_tree()

    def _formats_sort_key(self, f: core.FormatItem):
        col = self._sort_formats_col
        if col == "id":
            try:
                return int(f.format_id)
            except Exception:
                return f.format_id
        if col == "kind":
            return f.kind
        if col == "ext":
            return f.ext
        if col == "res":
            return (int(f.height or 0), int(f.width or 0))
        if col == "fps":
            return float(f.fps or 0.0)
        if col == "vcodec":
            return f.vcodec
        if col == "acodec":
            return f.acodec
        if col == "tbr":
            return float(f.tbr or f.abr or 0.0)
        if col == "size":
            return int(f.filesize or 0)
        return (f.format_note or "").lower()

    def _sort_formats(self, col: str) -> None:
        if col == self._sort_formats_col:
            self._sort_formats_desc = not self._sort_formats_desc
        else:
            self._sort_formats_col = col
            self._sort_formats_desc = col in ("res", "fps", "tbr", "size")
        self._refresh_formats_tree()

    def _clear_formats(self) -> None:
        self._formats = []
        for iid in self.tree_fmt.get_children():
            self.tree_fmt.delete(iid)
        self.var_format.set("")

    def _refresh_formats_tree(self) -> None:
        selected = set(str(x) for x in self.tree_fmt.selection())
        for iid in self.tree_fmt.get_children():
            self.tree_fmt.delete(iid)
        mp4_only = bool(self.var_filter_mp4.get())
        try:
            formats = sorted(self._formats, key=self._formats_sort_key, reverse=bool(self._sort_formats_desc))
        except Exception:
            formats = list(self._formats)
        for it in formats:
            if mp4_only and it.ext not in ("mp4", "m4a"):
                continue
            self.tree_fmt.insert(
                "",
                "end",
                iid=str(it.format_id),
                values=(
                    it.format_id,
                    it.kind,
                    it.ext,
                    core.format_resolution(it.width, it.height),
                    (f"{it.fps:.0f}" if it.fps else ""),
                    it.vcodec,
                    it.acodec,
                    core.format_bitrate_kbps(it.tbr or it.abr),
                    core.format_filesize(it.filesize),
                    it.format_note,
                ),
            )
        if selected:
            try:
                self.tree_fmt.selection_set([x for x in selected if x in set(self.tree_fmt.get_children())])
            except Exception:
                pass

    def _use_selected_format(self) -> None:
        sel = self.tree_fmt.selection()
        if not sel:
            return
        picked = [str(x) for x in sel if str(x)]
        if not picked:
            return

        if len(picked) >= 2:
            by_id = {f.format_id: f for f in self._formats}
            kinds = [by_id.get(i).kind if by_id.get(i) else "" for i in picked]
            if len(picked) == 2 and set(kinds) == {"v", "a"}:
                v_id = picked[kinds.index("v")]
                a_id = picked[kinds.index("a")]
                self.var_format.set(f"{v_id}+{a_id}")
            else:
                self.var_format.set("+".join(picked))
            self._sync_controls()
            return

        self.var_format.set(picked[0])
        self._sync_controls()

    def _start_info(self) -> None:
        if self._info_thread is not None and self._info_thread.is_alive():
            return
        url = (self.var_url.get() or self._selected_url or "").strip()
        if not url:
            messagebox.showwarning("yt-dlr", "Válassz egy videót a listából.")
            return
        try:
            ytdlp = core.resolve_ytdlp()
        except Exception as e:
            messagebox.showerror("yt-dlr", f"yt-dlp nem található.\n\n{e}\n\nTelepítés: brew install yt-dlp")
            return

        self.var_status.set("Formátumok lekérése…")
        self._append_log(f"Formats: {url}")

        def _worker() -> None:
            try:
                cmd = core.build_info_cmd(ytdlp=ytdlp, url=url, no_playlist=True)
                self._events.put(_UiEvent("log", core.quote_cmd(cmd)))
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=os.environ.copy())
                self._info_proc = proc
                out = proc.stdout
                text = ""
                if out is not None:
                    text = out.read() or ""
                rc = proc.wait()
                if rc != 0:
                    self._events.put(_UiEvent("error", f"Formátumlista sikertelen (exit={rc})."))
                    return
                info = core.parse_info_json(text)
                formats = core.extract_format_items(info)
                formats = sorted(
                    formats,
                    key=lambda f: (
                        0 if f.kind == "av" else (1 if f.kind == "v" else 2),
                        -(f.height or 0),
                        -(f.tbr or f.abr or 0.0),
                        f.ext,
                    ),
                )
                self._events.put(_UiEvent("formats", formats))
                self._events.put(_UiEvent("status", f"Formátumok: {len(formats)}"))
            except Exception as e:
                self._events.put(_UiEvent("error", f"Formátumlista hiba: {e}"))
            finally:
                self._info_proc = None
                self._events.put(_UiEvent("done", None))

        self._info_thread = threading.Thread(target=_worker, daemon=True)
        self._info_thread.start()
        self._sync_controls()

    def _cancel_info(self) -> None:
        proc = self._info_proc
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass

    def _start_search(self) -> None:
        if self._search_thread is not None and self._search_thread.is_alive():
            return
        query = (self.var_query.get() or "").strip()
        if not query:
            messagebox.showwarning("yt-dlr", "Írj be egy keresőkifejezést.")
            return
        try:
            ytdlp = core.resolve_ytdlp()
        except Exception as e:
            messagebox.showerror("yt-dlr", f"yt-dlp nem található.\n\n{e}\n\nTelepítés: brew install yt-dlp")
            return

        limit = int(self.var_limit.get() or 20)
        fast = bool(self.var_fast.get())

        self.var_status.set("Keresés…")
        self._append_log(f"Search: {query} (limit={limit}, fast={fast})")

        def _worker() -> None:
            try:
                cmd = core.build_search_cmd(ytdlp=ytdlp, query=query, limit=limit, fast=fast)
                self._events.put(_UiEvent("log", core.quote_cmd(cmd)))
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=os.environ.copy())
                self._search_proc = proc
                lines: list[str] = []
                out = proc.stdout
                if out is not None:
                    for raw in out:
                        line = str(raw or "").rstrip()
                        if not line:
                            continue
                        if line.lstrip().startswith("{"):
                            lines.append(line)
                        else:
                            self._events.put(_UiEvent("log", line))
                rc = proc.wait()
                if rc != 0:
                    self._events.put(_UiEvent("error", f"Keresés sikertelen (exit={rc})."))
                    return
                items = core.parse_search_items(lines)
                self._events.put(_UiEvent("results", items))
            except Exception as e:
                self._events.put(_UiEvent("error", f"Keresés hiba: {e}"))
            finally:
                self._search_proc = None
                self._events.put(_UiEvent("done", None))

        self._search_thread = threading.Thread(target=_worker, daemon=True)
        self._search_thread.start()
        self._sync_controls()

    def _cancel_search(self) -> None:
        proc = self._search_proc
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass

    def _start_download(self) -> None:
        if self._download_thread is not None and self._download_thread.is_alive():
            return
        url = (self.var_url.get() or self._selected_url or "").strip()
        if not url:
            messagebox.showwarning("yt-dlr", "Válassz egy videót a listából (vagy legyen URL).")
            return
        out_dir = (self.var_out_dir.get() or "").strip() or str(Path.home() / "Downloads")
        try:
            Path(out_dir).expanduser().mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("yt-dlr", f"Nem hozható létre a mappa:\n{out_dir}\n\n{e}")
            return

        mode = (self.var_mode.get() or "av").strip().lower()
        single = bool(self.var_single_file.get())
        no_playlist = bool(self.var_no_playlist.get())
        template = (self.var_template.get() or core.DEFAULT_TEMPLATE).strip()
        extra = _split_args(self.var_extra.get())
        merge = (self.var_merge.get() or "auto").strip().lower()
        merge_fmt = None if merge in ("", "auto") else merge
        fmt_sel = (self.var_format.get() or "").strip()
        if (not fmt_sel) and (mode == "av") and bool(self.var_prefer_recordbox.get()) and (not single):
            fmt_sel = core.recordbox_recommended_format_selector()
            if merge_fmt is None:
                merge_fmt = "mp4"

        try:
            ytdlp = core.resolve_ytdlp()
        except Exception as e:
            messagebox.showerror("yt-dlr", f"yt-dlp nem található.\n\n{e}\n\nTelepítés: brew install yt-dlp")
            return

        ffmpeg_location = None
        need_ffmpeg = False
        if fmt_sel:
            need_ffmpeg = ("+" in fmt_sel) or ("bestvideo" in fmt_sel and "bestaudio" in fmt_sel)
        else:
            need_ffmpeg = (mode == "av" and not single)
        if need_ffmpeg:
            ffmpeg_location = core.resolve_ffmpeg_location()  # may be None
            if ffmpeg_location is None:
                messagebox.showerror("yt-dlr", "FFmpeg kell a best A/V merge-hez.\n\nTelepítés: brew install ffmpeg")
                return

        self.var_status.set("Letöltés…")
        self._append_log(f"Download: {url}")

        def _worker() -> None:
            final_path: str | None = None
            try:
                cmd = core.build_download_cmd(
                    ytdlp=ytdlp,
                    urls=[url],
                    out_dir=str(Path(out_dir).expanduser()),
                    template=template,
                    mode=mode,
                    format_selector=(fmt_sel or None),
                    single_file=single,
                    no_playlist=no_playlist,
                    ffmpeg_location=ffmpeg_location,
                    merge_output_format=merge_fmt,
                    passthrough=extra,
                )
                self._events.put(_UiEvent("log", core.quote_cmd(cmd)))
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=os.environ.copy())
                self._download_proc = proc

                out = proc.stdout
                if out is not None:
                    for raw in out:
                        line = str(raw or "").rstrip()
                        if not line:
                            continue
                        self._events.put(_UiEvent("log", line))
                        if ("/" in line or "\\\\" in line) and (len(line) < 1024) and Path(line).suffix:
                            final_path = line
                            self._events.put(_UiEvent("status", f"Letöltve: {final_path}"))

                rc = proc.wait()
                if rc != 0:
                    self._events.put(_UiEvent("error", f"Letöltés sikertelen (exit={rc})."))
                    return
                if final_path:
                    self._events.put(_UiEvent("status", f"Kész: {final_path}"))
                else:
                    self._events.put(_UiEvent("status", "Kész."))
            except Exception as e:
                self._events.put(_UiEvent("error", f"Letöltés hiba: {e}"))
            finally:
                self._download_proc = None
                self._events.put(_UiEvent("done", None))

        self._download_thread = threading.Thread(target=_worker, daemon=True)
        self._download_thread.start()
        self._sync_controls()

    def _cancel_download(self) -> None:
        proc = self._download_proc
        if proc is None:
            return
        try:
            proc.terminate()
            self.var_status.set("Cancel…")
        except Exception:
            pass

    def _dj_download(self) -> None:
        url = (self.var_url.get() or self._selected_url or "").strip()
        if not url:
            messagebox.showwarning("yt-dlr", "Válassz egy videót a listából.")
            return

        dj_dir = (self.var_dj_dir.get() or "").strip()
        if not dj_dir:
            messagebox.showwarning("yt-dlr", "Adj meg egy Rekordbox watch folder mappát.")
            return

        try:
            Path(dj_dir).expanduser().mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("yt-dlr", f"Nem hozható létre a mappa:\n{dj_dir}\n\n{e}")
            return

        self.var_out_dir.set(dj_dir)
        self.var_mode.set("audio")
        self.var_format.set(core.recordbox_audio_m4a_format_selector())
        self.var_no_playlist.set(True)
        self.var_status.set("DJ Rush: m4a letöltés…")
        self._start_download()

    def _default_dj_dir(self) -> Path:
        candidates = [
            Path.home() / "Music" / "Rekordbox" / "Auto Import",
            Path.home() / "Music" / "rekordbox" / "Auto Import",
            Path.home() / "Music" / "Rekordbox",
            Path.home() / "Music",
            Path.home() / "Downloads",
        ]
        for p in candidates:
            try:
                if p.exists():
                    return p
            except Exception:
                continue
        return Path.home() / "Downloads"

    def _start_preview(self) -> None:
        if self._preview_thread is not None and self._preview_thread.is_alive():
            return
        url = (self.var_url.get() or self._selected_url or "").strip()
        if not url:
            messagebox.showwarning("yt-dlr", "Válassz egy videót a listából.")
            return
        fmt_sel = (self.var_format.get() or "").strip()
        mode = (self.var_mode.get() or "av").strip().lower()
        single = bool(self.var_single_file.get())
        if (not fmt_sel) and (mode == "av") and bool(self.var_prefer_recordbox.get()) and (not single):
            fmt_sel = core.recordbox_recommended_format_selector()

        mpv = shutil.which("mpv")
        ffplay = shutil.which("ffplay")

        self.var_status.set("Preview…")
        self._append_log(f"Preview: {url}")

        def _worker() -> None:
            try:
                if mpv:
                    try:
                        ytdlp = core.resolve_ytdlp()
                    except Exception as e:
                        raise RuntimeError(f"yt-dlp not found: {e}") from e

                    # Resolve direct media URL(s) via yt-dlp. This is more reliable than mpv's ytdl hook in GUI contexts.
                    preview_fmt = fmt_sel
                    if not preview_fmt:
                        if mode == "audio":
                            preview_fmt = core.recordbox_audio_m4a_format_selector()
                        elif mode == "video":
                            preview_fmt = "bestvideo/best"
                        else:
                            preview_fmt = "best*[vcodec!=none][acodec!=none]/best"

                    get_url_cmd = core.build_get_url_cmd(
                        ytdlp=ytdlp,
                        url=url,
                        format_selector=preview_fmt,
                        no_playlist=True,
                    )
                    self._events.put(_UiEvent("log", core.quote_cmd(get_url_cmd)))
                    p = subprocess.run(get_url_cmd, check=False, text=True, capture_output=True, env=os.environ.copy())
                    if p.returncode != 0:
                        msg = (p.stderr or p.stdout or "").strip()
                        raise RuntimeError(f"Cannot resolve preview URL (exit={p.returncode}). {msg[:240] if msg else ''}".strip())
                    direct_urls = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
                    if not direct_urls:
                        raise RuntimeError("Cannot resolve preview URL (empty output).")

                    main_url = direct_urls[0]
                    audio_url = direct_urls[1] if len(direct_urls) >= 2 else ""

                    def _run_mpv(*, wid: int | None) -> tuple[int, list[str]]:
                        # mpv/FFmpeg can be very chatty (e.g. H.264 "Late SEI..." warnings); keep UI/terminal quiet by default.
                        cmd = [mpv, "--force-window=yes", "--keep-open=no", "--no-terminal", "--msg-level=ffmpeg=error"]
                        if wid is not None and wid > 0:
                            cmd.append(f"--wid={wid}")
                        cmd.append(main_url)
                        if audio_url:
                            cmd.append(f"--audio-file={audio_url}")

                        self._events.put(_UiEvent("log", " ".join(shlex.quote(x) for x in cmd)))
                        proc = subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1,
                            env=os.environ.copy(),
                        )
                        self._preview_proc = proc
                        tail: list[str] = []
                        out = proc.stdout
                        if out is not None:
                            for raw in out:
                                line = str(raw or "").rstrip()
                                if not line:
                                    continue
                                tail.append(line)
                                if len(tail) > 40:
                                    tail = tail[-40:]
                                # mpv can be chatty; keep only key lines in the UI log
                                if ("error" in line.lower()) or ("failed" in line.lower()) or ("ytdl" in line.lower()):
                                    self._events.put(_UiEvent("log", f"[mpv] {line}"))
                        rc = proc.wait()
                        return rc, tail

                    embed = bool(self.var_embed_preview.get())
                    wid: int | None = None
                    if embed:
                        try:
                            self.preview_host.update_idletasks()
                            self.update_idletasks()
                            wid = int(self.preview_host.winfo_id())
                        except Exception:
                            wid = None

                    if embed and wid:
                        self._events.put(_UiEvent("status", "Preview (embedded)…"))
                        rc, tail = _run_mpv(wid=wid)
                        if rc == 0:
                            return
                        self._events.put(_UiEvent("log", f"[mpv] embed failed (exit={rc}); retry external window"))
                        # If it fails immediately, try again without embedding.
                        time.sleep(0.2)
                        rc2, tail2 = _run_mpv(wid=None)
                        if rc2 != 0:
                            msg = "\n".join((tail2 or tail)[-10:])
                            raise RuntimeError(f"mpv preview failed (exit={rc2}).\n{msg}".strip())
                        return

                    self._events.put(_UiEvent("status", "Preview…"))
                    rc, tail = _run_mpv(wid=None)
                    if rc != 0:
                        msg = "\n".join(tail[-10:])
                        raise RuntimeError(f"mpv preview failed (exit={rc}).\n{msg}".strip())
                    return

                if ffplay:
                    try:
                        ytdlp = core.resolve_ytdlp()
                    except Exception as e:
                        raise RuntimeError(f"yt-dlp not found: {e}") from e
                    # ffplay nem tud YouTube URL-t; kérünk egy egyfájlos direct URL-t preview-hoz.
                    get_url_cmd = core.build_get_url_cmd(
                        ytdlp=ytdlp,
                        url=url,
                        format_selector="best*[vcodec!=none][acodec!=none]/best",
                        no_playlist=True,
                    )
                    self._events.put(_UiEvent("log", core.quote_cmd(get_url_cmd)))
                    p = subprocess.run(get_url_cmd, check=False, text=True, capture_output=True, env=os.environ.copy())
                    direct = (p.stdout or "").strip().splitlines()
                    if p.returncode != 0 or not direct:
                        msg = (p.stderr or p.stdout or "").strip()
                        raise RuntimeError(f"Cannot resolve direct URL (exit={p.returncode}). {msg[:200] if msg else ''}".strip())
                    direct_url = direct[0].strip()
                    # Silence non-actionable decoder warnings (e.g. H.264 "Late SEI...") during preview.
                    cmd = [ffplay, "-hide_banner", "-nostats", "-loglevel", "error", direct_url]
                    self._events.put(_UiEvent("log", " ".join(shlex.quote(x) for x in cmd)))
                    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=os.environ.copy())
                    self._preview_proc = proc
                    proc.wait()
                    return

                self._events.put(_UiEvent("error", "Preview-hoz telepíts mpv-t (ajánlott) vagy ffplay-t (FFmpeg)."))
            except Exception as e:
                self._events.put(_UiEvent("error", f"Preview hiba: {e}"))
            finally:
                self._preview_proc = None
                self._events.put(_UiEvent("done", None))

        self._preview_thread = threading.Thread(target=_worker, daemon=True)
        self._preview_thread.start()
        self._sync_controls()

    def _stop_preview(self) -> None:
        proc = self._preview_proc
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass

    def _on_close(self) -> None:
        for p in (self._search_proc, self._info_proc, self._download_proc, self._preview_proc):
            try:
                if p is not None:
                    p.terminate()
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass


def run_gui() -> None:
    app = YtDlrGui()
    app.mainloop()


if __name__ == "__main__":
    run_gui()
