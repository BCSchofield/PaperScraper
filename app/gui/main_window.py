"""Root application window."""

from __future__ import annotations

import customtkinter as ctk

from app.config import APP_NAME, APP_VERSION, COLORS, WINDOW_SIZE, WINDOW_MIN_SIZE, FONT_FAMILY
from app.storage import history as db


class MainWindow(ctk.CTk):
    def __init__(self):
        super().__init__()

        db.init_db()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_NAME}  ·  v{APP_VERSION}")
        self.geometry(WINDOW_SIZE)
        self.minsize(*WINDOW_MIN_SIZE)
        self.configure(fg_color=COLORS["bg_primary"])

        # ── Title bar ─────────────────────────────────────────────────────────
        title_bar = ctk.CTkFrame(self, fg_color=COLORS["bg_secondary"], height=56, corner_radius=0)
        title_bar.pack(fill="x", side="top")
        title_bar.pack_propagate(False)

        ctk.CTkLabel(
            title_bar,
            text=APP_NAME,
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left", padx=20, pady=14)

        ctk.CTkLabel(
            title_bar,
            text=f"v{APP_VERSION}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_muted"],
        ).pack(side="left", pady=14)

        # ── Tab view ──────────────────────────────────────────────────────────
        self.tabs = ctk.CTkTabview(
            self,
            fg_color=COLORS["bg_primary"],
            segmented_button_fg_color=COLORS["bg_secondary"],
            segmented_button_selected_color=COLORS["accent"],
            segmented_button_selected_hover_color=COLORS["accent_hover"],
            segmented_button_unselected_color=COLORS["bg_secondary"],
            segmented_button_unselected_hover_color=COLORS["bg_tertiary"],
            text_color=COLORS["text_primary"],
            text_color_disabled=COLORS["text_muted"],
            corner_radius=0,
        )
        self.tabs.pack(fill="both", expand=True)

        self.tabs.add("Search")
        self.tabs.add("Sentiment Analysis")

        # Lazy import to avoid circular deps at module level
        from app.gui.search_tab import SearchTab
        from app.gui.sentiment_tab import SentimentTab

        self._search_tab = SearchTab(self.tabs.tab("Search"))
        self._search_tab.pack(fill="both", expand=True)

        self._sentiment_tab = SentimentTab(
            self.tabs.tab("Sentiment Analysis"),
            get_papers_fn=self._search_tab.get_current_papers,
        )
        self._sentiment_tab.pack(fill="both", expand=True)
