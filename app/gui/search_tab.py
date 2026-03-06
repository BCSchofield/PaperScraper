"""
Search tab — main scraping UI.

Layout:
  ┌─ Left sidebar (280px) ──┬─ Right main area ─────────────────────────┐
  │  Search input           │  Source progress bars                      │
  │  Active term chips      │  Results table (scrollable)                │
  │  Past searches list     │  Bottom bar: stats + export button         │
  │  Settings section       │                                            │
  └─────────────────────────┴────────────────────────────────────────────┘
"""

from __future__ import annotations

import threading
from pathlib import Path
from tkinter import filedialog
from typing import Optional

import customtkinter as ctk

from app.config import COLORS, FONT_FAMILY, SOURCES, DEFAULT_MAX_RESULTS, DEFAULT_THREAD_WORKERS
from app.scraper.search_manager import run_search
from app.export.excel_exporter import export
from app.storage import history as db


# ── Small reusable widgets ────────────────────────────────────────────────────

class _Chip(ctk.CTkFrame):
    """A removable tag chip for a search term."""

    def __init__(self, parent, text: str, on_remove):
        super().__init__(
            parent,
            fg_color=COLORS["tag_bg"],
            corner_radius=14,
            border_width=1,
            border_color=COLORS["tag_border"],
        )
        ctk.CTkLabel(
            self, text=text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["accent"],
        ).pack(side="left", padx=(10, 2), pady=4)
        ctk.CTkButton(
            self, text="×", width=20, height=20,
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            text_color=COLORS["text_muted"],
            hover_color=COLORS["bg_tertiary"],
            command=on_remove,
        ).pack(side="left", padx=(0, 6), pady=4)


class _SourceRow(ctk.CTkFrame):
    """One progress row for a single database source."""

    def __init__(self, parent, label: str):
        super().__init__(parent, fg_color="transparent")
        self.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self, text=label, width=80,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self._bar = ctk.CTkProgressBar(
            self, height=6,
            fg_color=COLORS["bg_tertiary"],
            progress_color=COLORS["accent"],
            corner_radius=3,
        )
        self._bar.set(0)
        self._bar.grid(row=0, column=1, sticky="ew")

        self._status = ctk.CTkLabel(
            self, text="idle", width=56,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_muted"],
            anchor="e",
        )
        self._status.grid(row=0, column=2, sticky="e", padx=(8, 0))

    def set_searching(self):
        self._bar.configure(mode="indeterminate", progress_color=COLORS["accent"])
        self._bar.start()
        self._status.configure(text="searching…", text_color=COLORS["accent"])

    def set_done(self, count: int):
        self._bar.stop()
        self._bar.configure(mode="determinate", progress_color=COLORS["success"])
        self._bar.set(1)
        self._status.configure(text=f"{count} found", text_color=COLORS["success"])

    def set_error(self):
        self._bar.stop()
        self._bar.configure(mode="determinate", progress_color=COLORS["error"])
        self._bar.set(1)
        self._status.configure(text="error", text_color=COLORS["error"])

    def reset(self):
        self._bar.stop()
        self._bar.configure(mode="determinate", progress_color=COLORS["accent"])
        self._bar.set(0)
        self._status.configure(text="idle", text_color=COLORS["text_muted"])


# ── Main SearchTab ─────────────────────────────────────────────────────────────

class SearchTab(ctk.CTkFrame):

    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_primary"], corner_radius=0)

        self._terms: list[str] = []
        self._results: dict[str, list[dict]] = {}  # term → papers
        self._chips: dict[str, _Chip] = {}
        self._source_rows: dict[str, _SourceRow] = {}
        self._search_running = False
        self._output_dir: str = str(Path.home() / "Desktop")

        # Restore saved output dir
        saved_dir = db.load_setting("output_dir")
        if saved_dir and Path(saved_dir).exists():
            self._output_dir = saved_dir

        self._build_layout()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_layout(self):
        # Outer two-column grid
        self.columnconfigure(0, weight=0, minsize=290)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._sidebar = ctk.CTkFrame(
            self, fg_color=COLORS["bg_secondary"],
            corner_radius=0, width=290,
        )
        self._sidebar.grid(row=0, column=0, sticky="nsew")
        self._sidebar.grid_propagate(False)

        self._main = ctk.CTkFrame(self, fg_color=COLORS["bg_primary"], corner_radius=0)
        self._main.grid(row=0, column=1, sticky="nsew")

        self._build_sidebar()
        self._build_main()

    def _build_sidebar(self):
        sb = self._sidebar
        pad = {"padx": 16, "pady": (10, 4)}

        # ── Action buttons — packed FIRST with side="bottom" so they are
        #    always visible regardless of the expanding history frame above.
        btn_frame = ctk.CTkFrame(sb, fg_color="transparent")
        btn_frame.pack(side="bottom", fill="x", padx=16, pady=12)
        btn_frame.columnconfigure((0, 1), weight=1)

        self._search_btn = ctk.CTkButton(
            btn_frame, text="Search", height=40,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            corner_radius=10,
            command=self._start_search,
        )
        self._search_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self._clear_btn = ctk.CTkButton(
            btn_frame, text="Clear", height=40,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_input"],
            text_color=COLORS["text_secondary"],
            corner_radius=10,
            command=self._clear_results,
        )
        self._clear_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        # Separator above buttons (also bottom-anchored)
        ctk.CTkFrame(sb, fg_color=COLORS["separator"], height=1, corner_radius=0).pack(
            side="bottom", fill="x", padx=12, pady=(0, 4)
        )

        # ── Everything below packs top-down ───────────────────────────────────

        # ── Search input ──────────────────────────────────────────────────────
        ctk.CTkLabel(
            sb, text="Search Terms",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"], anchor="w",
        ).pack(fill="x", **pad)

        input_row = ctk.CTkFrame(sb, fg_color="transparent")
        input_row.pack(fill="x", padx=16, pady=(0, 8))
        input_row.columnconfigure(0, weight=1)

        self._term_entry = ctk.CTkEntry(
            input_row,
            placeholder_text="e.g. machine learning cancer",
            fg_color=COLORS["bg_input"],
            border_color=COLORS["separator"],
            text_color=COLORS["text_primary"],
            placeholder_text_color=COLORS["text_muted"],
            corner_radius=8,
            height=36,
        )
        self._term_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._term_entry.bind("<Return>", lambda _: self._add_term())

        ctk.CTkButton(
            input_row, text="+", width=36, height=36,
            font=ctk.CTkFont(size=18),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            corner_radius=8,
            command=self._add_term,
        ).grid(row=0, column=1)

        # ── Active chips area (plain frame — CTkScrollableFrame corrupts on
        #    winfo_children() clear, so we use a regular CTkFrame and track
        #    chips explicitly in self._chips) ──────────────────────────────────
        self._chips_frame = ctk.CTkFrame(sb, fg_color="transparent")
        self._chips_frame.pack(fill="x", padx=16, pady=(0, 6))

        _sep(sb)

        # ── Search settings ───────────────────────────────────────────────────
        ctk.CTkLabel(
            sb, text="Settings",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"], anchor="w",
        ).pack(fill="x", **pad)

        _lbl(sb, "Max results per source")
        self._max_results_var = ctk.IntVar(value=db.load_setting("max_results", DEFAULT_MAX_RESULTS))
        slider_row = ctk.CTkFrame(sb, fg_color="transparent")
        slider_row.pack(fill="x", padx=16, pady=(0, 6))
        slider_row.columnconfigure(0, weight=1)
        self._results_slider = ctk.CTkSlider(
            slider_row, from_=10, to=200, number_of_steps=19,
            variable=self._max_results_var,
            fg_color=COLORS["bg_tertiary"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            progress_color=COLORS["accent"],
        )
        self._results_slider.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            slider_row, textvariable=self._max_results_var, width=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).grid(row=0, column=1, padx=(8, 0))
        self._max_results_var.trace_add("write", lambda *_: db.save_setting("max_results", self._max_results_var.get()))

        _lbl(sb, "Concurrent sources")
        self._workers_var = ctk.IntVar(value=db.load_setting("workers", DEFAULT_THREAD_WORKERS))
        workers_row = ctk.CTkFrame(sb, fg_color="transparent")
        workers_row.pack(fill="x", padx=16, pady=(0, 6))
        workers_row.columnconfigure(0, weight=1)
        ctk.CTkSlider(
            workers_row, from_=1, to=5, number_of_steps=4,
            variable=self._workers_var,
            fg_color=COLORS["bg_tertiary"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            progress_color=COLORS["accent"],
        ).grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            workers_row, textvariable=self._workers_var, width=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).grid(row=0, column=1, padx=(8, 0))
        self._workers_var.trace_add("write", lambda *_: db.save_setting("workers", self._workers_var.get()))

        _lbl(sb, "Export folder")
        dir_row = ctk.CTkFrame(sb, fg_color="transparent")
        dir_row.pack(fill="x", padx=16, pady=(0, 6))
        dir_row.columnconfigure(0, weight=1)
        self._dir_label = ctk.CTkLabel(
            dir_row,
            text=self._short_path(self._output_dir),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_muted"],
            anchor="w",
        )
        self._dir_label.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            dir_row, text="Browse", width=64, height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_input"],
            text_color=COLORS["text_secondary"],
            corner_radius=6,
            command=self._choose_dir,
        ).grid(row=0, column=1, padx=(8, 0))

        _sep(sb)

        # ── Past searches ─────────────────────────────────────────────────────
        ctk.CTkLabel(
            sb, text="Recent Searches",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"], anchor="w",
        ).pack(fill="x", **pad)

        # expand=True is safe here because buttons are already anchored to bottom
        self._history_frame = ctk.CTkScrollableFrame(
            sb, fg_color="transparent",
            scrollbar_button_color=COLORS["bg_tertiary"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        self._history_frame.pack(fill="both", expand=True, padx=16, pady=(0, 6))

        self._refresh_history()

    def _build_main(self):
        m = self._main
        m.rowconfigure(1, weight=1)
        m.columnconfigure(0, weight=1)

        # ── Source progress panel ─────────────────────────────────────────────
        progress_panel = ctk.CTkFrame(m, fg_color=COLORS["bg_secondary"], corner_radius=12)
        progress_panel.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))

        ctk.CTkLabel(
            progress_panel, text="Sources",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_muted"],
            anchor="w",
        ).pack(fill="x", padx=16, pady=(10, 4))

        sources_grid = ctk.CTkFrame(progress_panel, fg_color="transparent")
        sources_grid.pack(fill="x", padx=16, pady=(0, 10))
        sources_grid.columnconfigure((0, 1), weight=1)

        source_keys = list(SOURCES.keys())
        for i, key in enumerate(source_keys):
            row_widget = _SourceRow(sources_grid, SOURCES[key])
            row_widget.grid(row=i // 2, column=i % 2, sticky="ew", padx=6, pady=3)
            self._source_rows[key] = row_widget

        # Overall progress
        self._overall_progress = ctk.CTkProgressBar(
            progress_panel, height=4,
            fg_color=COLORS["bg_tertiary"],
            progress_color=COLORS["accent"],
            corner_radius=2,
        )
        self._overall_progress.set(0)
        self._overall_progress.pack(fill="x", padx=16, pady=(0, 10))

        # ── Results table ─────────────────────────────────────────────────────
        table_frame = ctk.CTkFrame(m, fg_color=COLORS["bg_secondary"], corner_radius=12)
        table_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 6))
        table_frame.rowconfigure(1, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # Column headers
        self._header_row = ctk.CTkFrame(table_frame, fg_color=COLORS["bg_tertiary"], height=36, corner_radius=0)
        self._header_row.grid(row=0, column=0, sticky="ew")
        self._header_row.grid_propagate(False)
        self._build_table_headers()

        # Scrollable rows
        self._table_scroll = ctk.CTkScrollableFrame(
            table_frame, fg_color="transparent",
            scrollbar_button_color=COLORS["bg_tertiary"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        self._table_scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        self._table_scroll.columnconfigure(0, weight=1)

        self._empty_label = ctk.CTkLabel(
            self._table_scroll,
            text="Enter search terms and click Search to begin.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_muted"],
        )
        self._empty_label.pack(pady=60)

        # ── Bottom bar ────────────────────────────────────────────────────────
        bottom_bar = ctk.CTkFrame(m, fg_color=COLORS["bg_secondary"], corner_radius=12, height=50)
        bottom_bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 12))
        bottom_bar.grid_propagate(False)
        bottom_bar.columnconfigure(0, weight=1)

        self._stats_label = ctk.CTkLabel(
            bottom_bar, text="No results yet.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_muted"],
            anchor="w",
        )
        self._stats_label.grid(row=0, column=0, sticky="w", padx=16, pady=12)

        self._export_btn = ctk.CTkButton(
            bottom_bar, text="Export to Excel", width=160, height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["success"],
            hover_color="#28b84e",
            text_color="#000000",
            corner_radius=8,
            command=self._export,
            state="disabled",
        )
        self._export_btn.grid(row=0, column=1, padx=(0, 16), pady=8)

    def _build_table_headers(self):
        cols = [
            ("Term", 110), ("Title", 260), ("Authors", 160),
            ("Abstract", 340), ("Source(s)", 100),
        ]
        for col_idx, (name, width) in enumerate(cols):
            ctk.CTkLabel(
                self._header_row, text=name, width=width,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
                text_color=COLORS["text_muted"],
                anchor="w",
            ).pack(side="left", padx=(12 if col_idx == 0 else 4, 0))

    # ── Term management ────────────────────────────────────────────────────────

    def _add_term(self):
        term = self._term_entry.get().strip()
        if not term or term in self._terms:
            self._term_entry.delete(0, "end")
            return
        self._terms.append(term)
        self._term_entry.delete(0, "end")
        self._render_chips()

    def _remove_term(self, term: str):
        if term in self._terms:
            self._terms.remove(term)
        self._render_chips()

    def _render_chips(self):
        # Destroy only our tracked chip widgets — never use winfo_children() on
        # a CTkScrollableFrame as it returns internal canvas/scrollbar widgets.
        for chip in list(self._chips.values()):
            chip.destroy()
        self._chips.clear()
        for term in self._terms:
            chip = _Chip(self._chips_frame, term, lambda t=term: self._remove_term(t))
            chip.pack(anchor="w", pady=2, fill="x")
            self._chips[term] = chip

    # ── History ────────────────────────────────────────────────────────────────

    def _refresh_history(self):
        # Destroy tracked history row widgets explicitly — safe for CTkScrollableFrame
        for w in getattr(self, "_history_rows", []):
            try:
                w.destroy()
            except Exception:
                pass
        self._history_rows = []
        for term in db.get_search_history(25):
            row = ctk.CTkFrame(self._history_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            row.columnconfigure(0, weight=1)
            self._history_rows.append(row)
            ctk.CTkButton(
                row, text=term, anchor="w", height=28,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color="transparent",
                hover_color=COLORS["bg_tertiary"],
                text_color=COLORS["text_secondary"],
                corner_radius=6,
                command=lambda t=term: self._load_from_history(t),
            ).grid(row=0, column=0, sticky="ew")
            ctk.CTkButton(
                row, text="×", width=24, height=24,
                font=ctk.CTkFont(size=12),
                fg_color="transparent",
                hover_color=COLORS["bg_tertiary"],
                text_color=COLORS["text_muted"],
                command=lambda t=term: self._delete_history(t),
            ).grid(row=0, column=1)

    def _load_from_history(self, term: str):
        if term not in self._terms:
            self._terms.append(term)
            self._render_chips()

    def _delete_history(self, term: str):
        db.delete_search_term(term)
        self._refresh_history()

    # ── Settings ───────────────────────────────────────────────────────────────

    def _choose_dir(self):
        d = filedialog.askdirectory(title="Select export folder", initialdir=self._output_dir)
        if d:
            self._output_dir = d
            db.save_setting("output_dir", d)
            self._dir_label.configure(text=self._short_path(d))

    @staticmethod
    def _short_path(path: str) -> str:
        p = Path(path)
        try:
            rel = p.relative_to(Path.home())
            return f"~/{rel}"
        except ValueError:
            parts = p.parts
            return str(Path(*parts[-2:])) if len(parts) > 2 else path

    # ── Search ─────────────────────────────────────────────────────────────────

    def _start_search(self):
        if self._search_running:
            return
        if not self._terms:
            self._flash_entry()
            return

        self._search_running = True
        self._search_btn.configure(text="Searching…", state="disabled")
        self._export_btn.configure(state="disabled")
        self._results.clear()
        self._clear_table()

        # Reset progress bars
        for row in self._source_rows.values():
            row.reset()
        self._overall_progress.set(0)

        total_ops = len(self._terms) * len(SOURCES)
        self._completed_ops = 0
        self._source_counts: dict[str, int] = {k: 0 for k in SOURCES}

        def progress_cb(source_key: str, status: str):
            """Called from worker threads — must schedule GUI updates on main thread."""
            def _update():
                row = self._source_rows.get(source_key)
                if row is None:
                    return
                if status == "searching":
                    row.set_searching()
                elif status == "done":
                    # Count will be updated after search completes; just mark done
                    row.set_done(self._source_counts.get(source_key, 0))
                    self._completed_ops += 1
                    self._overall_progress.set(self._completed_ops / total_ops)
                elif status == "error":
                    row.set_error()
                    self._completed_ops += 1
                    self._overall_progress.set(self._completed_ops / total_ops)
            self.after(0, _update)

        def _worker():
            max_r = self._max_results_var.get()
            workers = self._workers_var.get()

            for term in self._terms:
                papers = run_search(
                    term,
                    max_results=max_r,
                    max_workers=workers,
                    progress_callback=progress_cb,
                )
                self._results[term] = papers

                def _add_rows(t=term, ps=papers):
                    # Count only here (on main thread) to avoid double-counting
                    for key in SOURCES:
                        cnt = sum(1 for p in ps if SOURCES[key] in p.get("source", ""))
                        self._source_counts[key] = self._source_counts.get(key, 0) + cnt
                        src_row = self._source_rows.get(key)
                        if src_row:
                            src_row.set_done(self._source_counts[key])
                    self._add_results_to_table(t, ps)

                self.after(0, _add_rows)

                db.add_search_term(term)
                self.after(0, self._refresh_history)

            def _finish():
                self._search_running = False
                self._search_btn.configure(text="Search", state="normal")
                total = sum(len(v) for v in self._results.values())
                self._stats_label.configure(
                    text=f"{total} papers found across {len(self._terms)} search term(s).",
                    text_color=COLORS["text_secondary"],
                )
                if total > 0:
                    self._export_btn.configure(state="normal")
                self._overall_progress.set(1)

            self.after(0, _finish)

        threading.Thread(target=_worker, daemon=True).start()

    def _flash_entry(self):
        self._term_entry.configure(border_color=COLORS["error"])
        self.after(800, lambda: self._term_entry.configure(border_color=COLORS["separator"]))

    # ── Table rendering ────────────────────────────────────────────────────────

    def _clear_table(self):
        for w in self._table_scroll.winfo_children():
            w.destroy()
        self._empty_label = ctk.CTkLabel(
            self._table_scroll,
            text="Searching…",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_muted"],
        )
        self._empty_label.pack(pady=60)

    def _add_results_to_table(self, term: str, papers: list[dict]):
        # Remove placeholder label once we have real results
        if hasattr(self, "_empty_label") and self._empty_label.winfo_exists():
            self._empty_label.destroy()

        if not papers:
            ctk.CTkLabel(
                self._table_scroll,
                text=f'No results found for "{term}".',
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                text_color=COLORS["text_muted"],
            ).pack(anchor="w", padx=16, pady=6)
            return

        for idx, paper in enumerate(papers):
            alt = idx % 2 == 0
            bg = COLORS["bg_tertiary"] if alt else COLORS["bg_secondary"]

            row = ctk.CTkFrame(
                self._table_scroll,
                fg_color=bg,
                corner_radius=6,
                cursor="hand2",
            )
            row.pack(fill="x", padx=4, pady=2)
            row.columnconfigure(2, weight=1)

            # Term chip (small)
            ctk.CTkLabel(
                row, text=term[:14] + ("…" if len(term) > 14 else ""),
                width=106, anchor="w",
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["accent"],
            ).grid(row=0, column=0, padx=(10, 4), pady=8, sticky="nw")

            # Title
            ctk.CTkLabel(
                row, text=_trunc(paper.get("title", ""), 60),
                width=256, anchor="w", justify="left", wraplength=250,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                text_color=COLORS["text_primary"],
            ).grid(row=0, column=1, padx=4, pady=8, sticky="nw")

            # Authors
            ctk.CTkLabel(
                row, text=_trunc(paper.get("authors", ""), 40),
                width=156, anchor="w",
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_muted"],
            ).grid(row=0, column=2, padx=4, pady=8, sticky="nw")

            # Abstract (truncated)
            ctk.CTkLabel(
                row, text=_trunc(paper.get("abstract", ""), 140),
                anchor="w", justify="left", wraplength=330,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
            ).grid(row=0, column=3, padx=4, pady=8, sticky="nw")

            # Source badge
            ctk.CTkLabel(
                row, text=paper.get("source", ""),
                width=96, anchor="center",
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_muted"],
            ).grid(row=0, column=4, padx=(4, 10), pady=8, sticky="n")

            # Click → detail popup
            for widget in row.winfo_children():
                widget.bind("<Button-1>", lambda e, p=paper: self._show_detail(p))
            row.bind("<Button-1>", lambda e, p=paper: self._show_detail(p))

    def _show_detail(self, paper: dict):
        """Open a modal popup with the full paper details."""
        win = ctk.CTkToplevel(self)
        win.title(paper.get("title", "Paper Details")[:60])
        win.geometry("700x540")
        win.configure(fg_color=COLORS["bg_primary"])
        win.grab_set()

        scroll = ctk.CTkScrollableFrame(win, fg_color=COLORS["bg_primary"])
        scroll.pack(fill="both", expand=True, padx=20, pady=20)

        def _field(label, value):
            ctk.CTkLabel(
                scroll, text=label,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
                text_color=COLORS["text_muted"], anchor="w",
            ).pack(fill="x", pady=(10, 2))
            ctk.CTkLabel(
                scroll, text=value or "—",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_primary"],
                anchor="w", justify="left", wraplength=640,
            ).pack(fill="x")

        _field("Title", paper.get("title"))
        _field("Authors", paper.get("authors"))
        _field("Abstract", paper.get("abstract"))
        _field("DOI", paper.get("doi"))
        _field("Link", paper.get("url"))
        _field("Source(s)", paper.get("source"))

        ctk.CTkButton(
            win, text="Close", width=100,
            fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_input"],
            text_color=COLORS["text_secondary"],
            command=win.destroy,
        ).pack(pady=(0, 16))

    # ── Export ─────────────────────────────────────────────────────────────────

    def _export(self):
        if not self._results:
            return
        try:
            path = export(self._results, self._output_dir, include_sentiment=False)
            self._stats_label.configure(
                text=f"Exported to: {path}",
                text_color=COLORS["success"],
            )
        except Exception as exc:
            self._stats_label.configure(
                text=f"Export failed: {exc}",
                text_color=COLORS["error"],
            )

    def _clear_results(self):
        self._results.clear()
        self._terms.clear()
        self._render_chips()
        for row in self._source_rows.values():
            row.reset()
        self._overall_progress.set(0)
        for w in self._table_scroll.winfo_children():
            w.destroy()
        self._empty_label = ctk.CTkLabel(
            self._table_scroll,
            text="Enter search terms and click Search to begin.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_muted"],
        )
        self._empty_label.pack(pady=60)
        self._stats_label.configure(text="No results yet.", text_color=COLORS["text_muted"])
        self._export_btn.configure(state="disabled")

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_current_papers(self) -> list[dict]:
        """Return flat list of all current search results (for sentiment tab)."""
        all_papers = []
        for papers in self._results.values():
            all_papers.extend(papers)
        return all_papers


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trunc(text: str, n: int) -> str:
    text = text.strip()
    return text[:n] + "…" if len(text) > n else text


def _sep(parent):
    ctk.CTkFrame(parent, fg_color=COLORS["separator"], height=1, corner_radius=0).pack(
        fill="x", padx=12, pady=6
    )


def _lbl(parent, text: str):
    ctk.CTkLabel(
        parent, text=text,
        font=ctk.CTkFont(family=FONT_FAMILY, size=11),
        text_color=COLORS["text_muted"], anchor="w",
    ).pack(fill="x", padx=16, pady=(4, 2))
