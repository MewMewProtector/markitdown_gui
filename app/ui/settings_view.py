"""
Settings tab: folder paths, theme, custom accent colors, MarkItDown
options (LLM, DI, CU), plugin toggles.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import config
from .theme import DEFAULT_DARK_ACCENT, DEFAULT_LIGHT_ACCENT


class _ColorSwatchButton(QPushButton):
    """A square button that displays a color swatch and opens a picker."""

    color_changed = Signal(str)

    def __init__(self, initial_hex: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(QSize(34, 26))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hex = ""
        self.set_value(initial_hex)
        self.clicked.connect(self._pick)

    def value(self) -> str:
        return self._hex

    def set_value(self, hex_color: str) -> None:
        self._hex = (hex_color or "").strip().lower()
        if not self._hex.startswith("#"):
            self._hex = "#" + self._hex if self._hex else ""
        if self._hex:
            self.setStyleSheet(
                f"background-color: {self._hex}; border: 1px solid #888;"
            )
        else:
            self.setStyleSheet(
                "background: repeating-conic-gradient(#ddd 0% 25%, #f4f4f4 25% 50%) 50%/8px 8px;"
                " border: 1px solid #888;"
            )

    def _pick(self) -> None:
        start = QColor(self._hex) if self._hex else QColor("#2EA86A")
        chosen = QColorDialog.getColor(
            start, self.parentWidget(), "Выберите цвет", QColorDialog.ColorDialogOption.ShowAlphaChannel
        )
        if not chosen.isValid():
            return
        new = chosen.name()
        self.set_value(new)
        self.color_changed.emit(new)


class SettingsView(QWidget):
    """Configurable application settings. Pure UI, no business logic."""

    theme_changed = Signal(str)  # "light" | "dark" | "system"
    accent_changed = Signal(str, str)  # accent_hex, accent_dark_hex

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # --------------------------------------------------------------
        # Folders
        # --------------------------------------------------------------
        folders = QWidget(container)
        folders.setObjectName("Surface")
        fl = QVBoxLayout(folders)
        fl.setContentsMargins(16, 16, 16, 16)
        fl.setSpacing(8)
        fl.addWidget(self._section_label("Папки"))

        self.edit_watch = QLineEdit(folders)
        self.edit_watch.setPlaceholderText("Не выбрано")
        btn_watch = QPushButton("Выбрать…", folders)
        row = QHBoxLayout()
        row.addWidget(self.edit_watch, 1)
        row.addWidget(btn_watch)
        fl.addLayout(row)
        btn_watch.clicked.connect(
            lambda: self._pick_into(self.edit_watch, "Выберите папку сканирования")
        )

        self.edit_output = QLineEdit(folders)
        self.edit_output.setPlaceholderText("Рядом с исходным файлом")
        btn_output = QPushButton("Выбрать…", folders)
        row = QHBoxLayout()
        row.addWidget(self.edit_output, 1)
        row.addWidget(btn_output)
        fl.addLayout(row)
        btn_output.clicked.connect(
            lambda: self._pick_into(self.edit_output, "Выберите папку сохранения")
        )

        root.addWidget(folders)

        # --------------------------------------------------------------
        # Appearance
        # --------------------------------------------------------------
        appearance = QWidget(container)
        appearance.setObjectName("Surface")
        al = QVBoxLayout(appearance)
        al.setContentsMargins(16, 16, 16, 16)
        al.setSpacing(8)
        al.addWidget(self._section_label("Внешний вид"))

        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Тема:", appearance))
        self.combo_theme = QComboBox(appearance)
        self.combo_theme.addItems(["Светлая", "Тёмная", "Как система"])
        self.combo_theme.currentIndexChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self.combo_theme)
        theme_row.addStretch(1)
        al.addLayout(theme_row)

        # Accent colors row.
        al.addWidget(self._make_muted_label(
            "Цвет акцента — задаёт основной тон кнопок, вкладок и подсветки."
        ))

        # Light accent
        self.edit_light_hex = QLineEdit(appearance)
        self.edit_light_hex.setPlaceholderText(DEFAULT_LIGHT_ACCENT)
        self.edit_light_hex.setMaximumWidth(110)
        self.swatch_light = _ColorSwatchButton("", appearance)
        self.swatch_light.color_changed.connect(self._on_swatch_changed)
        btn_reset_light = QPushButton("Сброс", appearance)
        btn_reset_light.clicked.connect(lambda: self._reset_accent("light"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Светлая тема:", appearance))
        row.addWidget(self.swatch_light)
        row.addWidget(self.edit_light_hex)
        row.addWidget(btn_reset_light)
        row.addStretch(1)
        al.addLayout(row)

        # Dark accent
        self.edit_dark_hex = QLineEdit(appearance)
        self.edit_dark_hex.setPlaceholderText(DEFAULT_DARK_ACCENT)
        self.edit_dark_hex.setMaximumWidth(110)
        self.swatch_dark = _ColorSwatchButton("", appearance)
        self.swatch_dark.color_changed.connect(self._on_swatch_changed)
        btn_reset_dark = QPushButton("Сброс", appearance)
        btn_reset_dark.clicked.connect(lambda: self._reset_accent("dark"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Тёмная тема:", appearance))
        row.addWidget(self.swatch_dark)
        row.addWidget(self.edit_dark_hex)
        row.addWidget(btn_reset_dark)
        row.addStretch(1)
        al.addLayout(row)

        root.addWidget(appearance)

        # --------------------------------------------------------------
        # MarkItDown / API
        # --------------------------------------------------------------
        api = QWidget(container)
        api.setObjectName("Surface")
        apil = QVBoxLayout(api)
        apil.setContentsMargins(16, 16, 16, 16)
        apil.setSpacing(8)
        apil.addWidget(self._section_label("MarkItDown / API"))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.edit_api_key = QLineEdit(api)
        self.edit_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_api_key.setPlaceholderText("sk-…")
        form.addRow("OpenAI API key:", self.edit_api_key)

        self.edit_base_url = QLineEdit(api)
        self.edit_base_url.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("OpenAI base URL:", self.edit_base_url)

        self.edit_model = QLineEdit(api)
        self.edit_model.setPlaceholderText("gpt-4o-mini")
        form.addRow("Model:", self.edit_model)

        self.edit_prompt = QLineEdit(api)
        self.edit_prompt.setPlaceholderText("Use markdown formatting")
        form.addRow("Prompt:", self.edit_prompt)

        self.edit_docintel = QLineEdit(api)
        self.edit_docintel.setPlaceholderText("https://…cognitiveservices.azure.com/")
        form.addRow("Document Intelligence endpoint:", self.edit_docintel)

        self.edit_cu = QLineEdit(api)
        self.edit_cu.setPlaceholderText("https://…api.cognitive.microsoft.com/")
        form.addRow("Content Understanding endpoint:", self.edit_cu)

        self.edit_cu_id = QLineEdit(api)
        self.edit_cu_id.setPlaceholderText("analyzer-id")
        form.addRow("CU analyzer id:", self.edit_cu_id)

        apil.addLayout(form)

        self.cb_plugins = QCheckBox("Использовать плагины", api)
        self.cb_docintel = QCheckBox("Включить Document Intelligence", api)
        self.cb_cu = QCheckBox("Включить Content Understanding", api)
        self.cb_audio = QCheckBox("Включить аудио-транскрипцию", api)
        self.cb_youtube = QCheckBox("Включить YouTube-транскрипцию", api)
        for cb in (
            self.cb_plugins, self.cb_docintel, self.cb_cu,
            self.cb_audio, self.cb_youtube,
        ):
            apil.addWidget(cb)

        root.addWidget(api)

        # --------------------------------------------------------------
        # Save row
        # --------------------------------------------------------------
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.btn_save = QPushButton("Сохранить", container)
        self.btn_save.setObjectName("Primary")
        self.btn_save.clicked.connect(self.save)
        save_row.addWidget(self.btn_save)
        root.addLayout(save_row)

        root.addStretch(1)

        # Sync edit fields and swatches on typing.
        self.edit_light_hex.textChanged.connect(self._on_hex_typed)
        self.edit_dark_hex.textChanged.connect(self._on_hex_typed)

        self.load()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _section_label(text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet("font-weight: 600; font-size: 11pt;")
        return l

    @staticmethod
    def _make_muted_label(text: str) -> QLabel:
        l = QLabel(text)
        l.setObjectName("Muted")
        l.setWordWrap(True)
        return l

    def _pick_into(self, line_edit: QLineEdit, title: str) -> None:
        folder = QFileDialog.getExistingDirectory(self, title)
        if folder:
            line_edit.setText(folder)

    def _on_swatch_changed(self, _hex: str) -> None:
        self.edit_light_hex.blockSignals(True)
        self.edit_dark_hex.blockSignals(True)
        self.edit_light_hex.setText(self.swatch_light.value())
        self.edit_dark_hex.setText(self.swatch_dark.value())
        self.edit_light_hex.blockSignals(False)
        self.edit_dark_hex.blockSignals(False)
        # Emit immediately so the UI updates while the user fine-tunes.
        self.accent_changed.emit(self.swatch_light.value(), self.swatch_dark.value())

    def _on_hex_typed(self, _text: str) -> None:
        self.swatch_light.set_value(self.edit_light_hex.text())
        self.swatch_dark.set_value(self.edit_dark_hex.text())
        self.accent_changed.emit(self.swatch_light.value(), self.swatch_dark.value())

    def _reset_accent(self, which: str) -> None:
        if which == "light":
            self.edit_light_hex.blockSignals(True)
            self.edit_light_hex.setText("")
            self.edit_light_hex.blockSignals(False)
            self.swatch_light.set_value("")
        else:
            self.edit_dark_hex.blockSignals(True)
            self.edit_dark_hex.setText("")
            self.edit_dark_hex.blockSignals(False)
            self.swatch_dark.set_value("")
        self.accent_changed.emit(self.swatch_light.value(), self.swatch_dark.value())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def load(self) -> None:
        s = config.get_all_settings()
        self.edit_watch.setText(s.get("watch_folder", ""))
        self.edit_output.setText(s.get("output_folder", ""))

        theme = s.get("theme", "system")
        idx = {"light": 0, "dark": 1, "system": 2}.get(theme, 2)
        self.combo_theme.blockSignals(True)
        self.combo_theme.setCurrentIndex(idx)
        self.combo_theme.blockSignals(False)

        # Accent colors.
        accent_light = s.get("accent_color", "") or ""
        accent_dark = s.get("accent_color_dark", "") or ""
        self.edit_light_hex.blockSignals(True)
        self.edit_dark_hex.blockSignals(True)
        self.edit_light_hex.setText(accent_light)
        self.edit_dark_hex.setText(accent_dark)
        self.edit_light_hex.blockSignals(False)
        self.edit_dark_hex.blockSignals(False)
        self.swatch_light.set_value(accent_light)
        self.swatch_dark.set_value(accent_dark)

        self.edit_api_key.setText(s.get("llm_api_key", ""))
        self.edit_base_url.setText(s.get("llm_base_url", ""))
        self.edit_model.setText(s.get("llm_model", ""))
        self.edit_prompt.setText(s.get("llm_prompt", ""))
        self.edit_docintel.setText(s.get("docintel_endpoint", ""))
        self.edit_cu.setText(s.get("cu_endpoint", ""))
        self.edit_cu_id.setText(s.get("cu_analyzer_id", ""))

        self.cb_plugins.setChecked(s.get("use_plugins") == "1")
        self.cb_docintel.setChecked(s.get("enable_docintel") == "1")
        self.cb_cu.setChecked(s.get("enable_cu") == "1")
        self.cb_audio.setChecked(s.get("enable_audio") == "1")
        self.cb_youtube.setChecked(s.get("enable_youtube") == "1")

    def save(self) -> None:
        config.set_setting("watch_folder", self.edit_watch.text().strip())
        config.set_setting("output_folder", self.edit_output.text().strip())

        theme_map = {0: "light", 1: "dark", 2: "system"}
        config.set_setting("theme", theme_map[self.combo_theme.currentIndex()])

        config.set_setting("accent_color", self.edit_light_hex.text().strip())
        config.set_setting("accent_color_dark", self.edit_dark_hex.text().strip())

        config.set_setting("llm_api_key", self.edit_api_key.text().strip())
        config.set_setting("llm_base_url", self.edit_base_url.text().strip())
        config.set_setting("llm_model", self.edit_model.text().strip())
        config.set_setting("llm_prompt", self.edit_prompt.text().strip())
        config.set_setting("docintel_endpoint", self.edit_docintel.text().strip())
        config.set_setting("cu_endpoint", self.edit_cu.text().strip())
        config.set_setting("cu_analyzer_id", self.edit_cu_id.text().strip())

        config.set_setting("use_plugins", "1" if self.cb_plugins.isChecked() else "0")
        config.set_setting("enable_docintel", "1" if self.cb_docintel.isChecked() else "0")
        config.set_setting("enable_cu", "1" if self.cb_cu.isChecked() else "0")
        config.set_setting("enable_audio", "1" if self.cb_audio.isChecked() else "0")
        config.set_setting("enable_youtube", "1" if self.cb_youtube.isChecked() else "0")

    # ------------------------------------------------------------------
    def _on_theme_changed(self, index: int) -> None:
        theme_map = {0: "light", 1: "dark", 2: "system"}
        self.theme_changed.emit(theme_map[index])