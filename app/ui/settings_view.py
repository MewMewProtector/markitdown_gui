"""
Settings tab: folder paths, theme, custom accent colors, MarkItDown
options (LLM, DI, CU), plugin toggles.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QToolButton,
)

from ..core import config
from .theme import DEFAULT_DARK_ACCENT, DEFAULT_LIGHT_ACCENT


class _ConnectionSignals(QObject):
    finished = Signal(bool, str)


class _ConnectionTask(QRunnable):
    """Check an OpenAI-compatible endpoint without blocking the UI."""

    def __init__(self, settings: dict[str, str]):
        super().__init__()
        self.settings = settings
        self.signals = _ConnectionSignals()

    @Slot()
    def run(self) -> None:
        from ..core.image_descriptor import _make_openai_client_with_error

        client, error = _make_openai_client_with_error(self.settings)
        if client is None:
            self.signals.finished.emit(False, error or "Не удалось создать клиент")
            return
        try:
            client.models.list(timeout=10.0)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            detail = f"HTTP {status}: {exc}" if status else str(exc)
            self.signals.finished.emit(False, detail)
            return
        self.signals.finished.emit(True, "Подключение установлено")


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
    # Emitted AFTER settings are persisted to SQLite. The main window
    # shows a centered "Настройки сохранены" toast in response.
    settings_saved = Signal()
    # Emitted when save() refused to write because of invalid streaming
    # path configuration. Payload is a human-readable Russian message.
    save_failed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._connection_task: _ConnectionTask | None = None

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

        page_title = QLabel("Настройки", container)
        page_title.setObjectName("PageTitle")
        root.addWidget(page_title)
        page_subtitle = QLabel(
            "Настройте автоматизацию, внешний вид и подключение к моделям.",
            container,
        )
        page_subtitle.setObjectName("PageSubtitle")
        root.addWidget(page_subtitle)

        # --------------------------------------------------------------
        # Folders
        # --------------------------------------------------------------
        folders = QFrame(container)
        folders.setObjectName("Card")
        fl = QVBoxLayout(folders)
        fl.setContentsMargins(16, 16, 16, 16)
        fl.setSpacing(8)
        fl.addWidget(self._section_label("Папки"))
        fl.addWidget(self._make_muted_label(
            "Откуда брать новые файлы и куда сохранять готовый Markdown."
        ))

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

        # Streaming mode toggle lives in the Folders section because it
        # governs what happens to NEW files arriving in the watched
        # folder. When enabled, only files created/copied AFTER the
        # watcher was started are auto-converted. The result is written
        # to the external output folder declared below. The
        # `describe_images` toggle governs LLM descriptions per-file as
        # usual, so PDFs / DOCX still get inline-image descriptions when
        # both toggles are on.
        self.cb_streaming_mode = QCheckBox(
            "Стриминг: автоматически конвертировать новые файлы в .md", folders
        )
        self.cb_streaming_mode.setToolTip(
            "При включении этого режима любой поддерживаемый файл, "
            "появившийся в папке сканирования после запуска слежения, "
            "конвертируется в .md и записывается в указанную ниже "
            "папку сохранения. Если включено «Описывать картинки (LLM)», "
            "картинки в файлах также описываются."
        )
        fl.addWidget(self.cb_streaming_mode)

        # Streaming requires an external output folder (so a freshly
        # written .md cannot trigger another conversion). We make this
        # explicit instead of falling back to "next to the source".
        self._streaming_hint = QLabel(
            "Для стриминга обязательно задайте внешнюю папку сохранения - "
            "она не должна совпадать с папкой сканирования или "
            "находиться внутри неё. Тогда .md файлы не попадут обратно "
            "в папку сканирования и не вызовут бесконечный цикл.",
            folders,
        )
        self._streaming_hint.setObjectName("Muted")
        self._streaming_hint.setWordWrap(True)
        self._streaming_hint.hide()
        fl.addWidget(self._streaming_hint)

        # Inline error shown when the user clicks Save with an invalid
        # streaming combination. The label is hidden on a successful
        # save.
        self._streaming_error = QLabel("", folders)
        self._streaming_error.setObjectName("StreamingError")
        self._streaming_error.setWordWrap(True)
        self._streaming_error.setStyleSheet(
            "color: #c0392b; font-size: 10pt;"
        )
        self._streaming_error.hide()
        fl.addWidget(self._streaming_error)

        root.addWidget(folders)

        # --------------------------------------------------------------
        # Appearance
        # --------------------------------------------------------------
        appearance = QFrame(container)
        appearance.setObjectName("Card")
        al = QVBoxLayout(appearance)
        al.setContentsMargins(16, 16, 16, 16)
        al.setSpacing(8)
        al.addWidget(self._section_label("Внешний вид"))
        al.addWidget(self._make_muted_label(
            "Тема и акцент применяются сразу, чтобы результат было видно до сохранения."
        ))

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
        api = QFrame(container)
        api.setObjectName("Card")
        apil = QVBoxLayout(api)
        apil.setContentsMargins(16, 16, 16, 16)
        apil.setSpacing(8)
        apil.addWidget(self._section_label("Модель и описание изображений"))
        apil.addWidget(self._make_muted_label(
            "Подойдёт OpenAI или любой совместимый сервис, включая OpenRouter и Ollama."
        ))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.edit_api_key = QLineEdit(api)
        self.edit_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_api_key.setPlaceholderText("sk-…")
        key_widget = QWidget(api)
        key_layout = QHBoxLayout(key_widget)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(8)
        key_layout.addWidget(self.edit_api_key, 1)
        self.btn_reveal_key = QPushButton("Показать", key_widget)
        self.btn_reveal_key.setCheckable(True)
        self.btn_reveal_key.toggled.connect(self._toggle_api_key_visibility)
        key_layout.addWidget(self.btn_reveal_key)
        form.addRow("API-ключ:", key_widget)

        self.edit_base_url = QLineEdit(api)
        self.edit_base_url.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("Адрес API:", self.edit_base_url)

        self.edit_model = QLineEdit(api)
        self.edit_model.setPlaceholderText("gpt-4o-mini")
        form.addRow("Модель:", self.edit_model)

        self.edit_prompt = QLineEdit(api)
        self.edit_prompt.setPlaceholderText("Use markdown formatting")
        form.addRow("Промпт:", self.edit_prompt)

        self.edit_docintel = QLineEdit(api)
        self.edit_docintel.setPlaceholderText("https://…cognitiveservices.azure.com/")

        self.edit_cu = QLineEdit(api)
        self.edit_cu.setPlaceholderText("https://…api.cognitive.microsoft.com/")

        self.edit_cu_id = QLineEdit(api)
        self.edit_cu_id.setPlaceholderText("analyzer-id")

        apil.addLayout(form)

        # The "describe images" toggle sits ABOVE the plugin checkboxes so
        # it's the first thing the user sees after the LLM endpoint fields.
        # It is synced with the same widget in the pre-conversion dialog
        # (see ScanView.confirm_conversion).
        self.cb_describe_images = QCheckBox(
            "Описывать картинки (LLM)", api
        )
        apil.addWidget(self.cb_describe_images)

        connection_row = QHBoxLayout()
        self.btn_test_connection = QPushButton("Проверить подключение", api)
        self.btn_test_connection.clicked.connect(self._test_connection)
        connection_row.addWidget(self.btn_test_connection)
        self.lbl_connection = QLabel(
            "Ключ хранится в защищённом хранилище Windows"
            if config.secure_storage_available()
            else "Системное хранилище недоступно — используется локальная база",
            api,
        )
        self.lbl_connection.setObjectName("Muted")
        connection_row.addWidget(self.lbl_connection, 1)
        apil.addLayout(connection_row)

        self.btn_advanced = QToolButton(api)
        self.btn_advanced.setText("Расширенные параметры")
        self.btn_advanced.setCheckable(True)
        self.btn_advanced.setArrowType(Qt.ArrowType.RightArrow)
        self.btn_advanced.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.btn_advanced.toggled.connect(self._toggle_advanced)
        apil.addWidget(self.btn_advanced)

        self.advanced_container = QWidget(api)
        advanced_layout = QVBoxLayout(self.advanced_container)
        advanced_layout.setContentsMargins(8, 2, 0, 0)
        advanced_layout.setSpacing(8)
        advanced_form = QFormLayout()
        advanced_form.setHorizontalSpacing(12)
        advanced_form.setVerticalSpacing(8)
        advanced_form.addRow("Document Intelligence:", self.edit_docintel)
        advanced_form.addRow("Content Understanding:", self.edit_cu)
        advanced_form.addRow("ID анализатора CU:", self.edit_cu_id)
        advanced_layout.addLayout(advanced_form)

        self.cb_plugins = QCheckBox("Использовать плагины", api)
        self.cb_docintel = QCheckBox("Включить Document Intelligence", api)
        self.cb_cu = QCheckBox("Включить Content Understanding", api)
        self.cb_audio = QCheckBox("Включить аудио-транскрипцию", api)
        self.cb_youtube = QCheckBox("Включить YouTube-транскрипцию", api)
        for cb in (
            self.cb_plugins, self.cb_docintel, self.cb_cu,
            self.cb_audio, self.cb_youtube,
        ):
            advanced_layout.addWidget(cb)
        self.advanced_container.hide()
        apil.addWidget(self.advanced_container)

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

        # Streaming hint reacts live to either the toggle or the output path.
        self.cb_streaming_mode.toggled.connect(self._update_streaming_hint)
        self.edit_output.textChanged.connect(self._update_streaming_hint)

        # Ctrl+S saves settings regardless of which sub-widget has focus.
        self._save_shortcut = QShortcut(
            QKeySequence.StandardKey.Save, self
        )
        self._save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._save_shortcut.activated.connect(self.save)

        self.load()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _section_label(text: str) -> QLabel:
        l = QLabel(text)
        l.setObjectName("SectionTitle")
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

    def _toggle_api_key_visibility(self, visible: bool) -> None:
        self.edit_api_key.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        self.btn_reveal_key.setText("Скрыть" if visible else "Показать")

    def _toggle_advanced(self, expanded: bool) -> None:
        self.advanced_container.setVisible(expanded)
        self.btn_advanced.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )

    def _test_connection(self) -> None:
        if self._connection_task is not None:
            return
        settings = {
            "llm_api_key": self.edit_api_key.text().strip(),
            "llm_base_url": self.edit_base_url.text().strip(),
            "llm_model": self.edit_model.text().strip(),
        }
        if not settings["llm_api_key"]:
            self._on_connection_result(False, "Сначала введите API-ключ")
            return
        self.btn_test_connection.setEnabled(False)
        self.btn_test_connection.setText("Проверяю…")
        self.lbl_connection.setStyleSheet("")
        self.lbl_connection.setText("Соединение с API…")
        task = _ConnectionTask(settings)
        task.signals.finished.connect(self._on_connection_result)
        self._connection_task = task
        QThreadPool.globalInstance().start(task)

    @Slot(bool, str)
    def _on_connection_result(self, success: bool, message: str) -> None:
        self._connection_task = None
        self.btn_test_connection.setEnabled(True)
        self.btn_test_connection.setText("Проверить подключение")
        self.lbl_connection.setText(("✓ " if success else "✕ ") + message)
        self.lbl_connection.setStyleSheet(
            "color: #239B63;" if success else "color: #D9534F;"
        )

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
        self.cb_streaming_mode.setChecked(s.get("streaming_mode") == "1")
        self._update_streaming_hint()

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
        self.cb_describe_images.setChecked(s.get("describe_images") == "1")

    def save(self) -> None:
        """
        Validate, then persist.

        Streaming mode is gated by a path-validation step so that we
        never persist a half-config that combines `streaming_mode = 1`
        with an unusable pair of folders. If the configuration is
        rejected we emit `save_failed` and **leave the form open** with
        the relevant values untouched - existing keys (including the LLM
        secrets) are NOT cleared on a failed save.
        """
        watch = self.edit_watch.text().strip()
        output = self.edit_output.text().strip()
        streaming_on = self.cb_streaming_mode.isChecked()

        if streaming_on:
            ok, msg = config.validate_streaming_paths(watch, output)
            if not ok:
                # Surface the error inline AND emit it for the toast
                # bar; then abort before touching SQLite so existing
                # values stay intact.
                self._streaming_error.setText(msg)
                self._streaming_error.show()
                self.save_failed.emit(msg)
                return
        # Successful path: clear any leftover error message from a
        # previous attempt.
        self._streaming_error.hide()
        self._streaming_error.setText("")

        config.set_setting("watch_folder", watch)
        config.set_setting("output_folder", output)

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
        config.set_setting(
            "describe_images",
            "1" if self.cb_describe_images.isChecked() else "0",
        )
        config.set_setting(
            "streaming_mode",
            "1" if streaming_on else "0",
        )

        # Notify listeners (MainWindow) that persistence actually finished.
        self.settings_saved.emit()

    # ------------------------------------------------------------------
    def _update_streaming_hint(self) -> None:
        """
        Show the streaming hint whenever the toggle is on, regardless of
        whether an output folder is set. The hint now explains the
        external-folder requirement instead of the old "next to source"
        fallback.
        """
        if not hasattr(self, "_streaming_hint"):
            return
        show = self.cb_streaming_mode.isChecked()
        self._streaming_hint.setVisible(show)

    # ------------------------------------------------------------------
    def _on_theme_changed(self, index: int) -> None:
        theme_map = {0: "light", 1: "dark", 2: "system"}
        self.theme_changed.emit(theme_map[index])
