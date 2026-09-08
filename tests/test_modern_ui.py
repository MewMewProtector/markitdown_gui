"""Tests for the modern navigation, preview renderer, and secret storage."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestMarkdownRenderer(unittest.TestCase):
    def test_latex_is_rendered_to_mathml(self) -> None:
        from app.core.markdown_renderer import render_markdown_document

        source = r"""# Формулы

Строчная формула: $x_i^2 + \alpha$.

$$\frac{a}{b} = \sum_{k=1}^{n} k$$

Код не является формулой: `$not_math$`.
"""
        document = render_markdown_document(source)

        self.assertGreaterEqual(document.count("<math"), 2)
        self.assertIn("<mfrac", document)
        self.assertTrue("<msubsup" in document or "<msub" in document)
        self.assertIn("$not_math$", document)

    def test_raw_html_is_not_executable(self) -> None:
        from app.core.markdown_renderer import render_markdown_document

        document = render_markdown_document("<script>alert('x')</script>")
        self.assertNotIn("<script>", document)
        self.assertIn("&lt;script&gt;", document)


class TestSecureApiKey(unittest.TestCase):
    def test_api_key_is_not_written_to_sqlite_when_keyring_works(self) -> None:
        from app.core import config

        vault: dict[tuple[str, str], str] = {}

        def set_password(service: str, username: str, value: str) -> None:
            vault[(service, username)] = value

        def get_password(service: str, username: str) -> str | None:
            return vault.get((service, username))

        with tempfile.TemporaryDirectory() as tmp:
            db_dir = Path(tmp) / "state"
            db_path = db_dir / "state.db"
            config.close_db()
            with mock.patch.object(config, "_DB_DIR", db_dir), mock.patch.object(
                config, "_DB_PATH", db_path
            ), mock.patch("keyring.set_password", side_effect=set_password), mock.patch(
                "keyring.get_password", side_effect=get_password
            ):
                config.set_setting("llm_api_key", "super-secret")
                row = config.get_db().execute(
                    "SELECT value FROM settings WHERE key='llm_api_key'"
                ).fetchone()
                self.assertEqual(row["value"], "")
                self.assertEqual(config.get_setting("llm_api_key"), "super-secret")
                config.close_db()


class TestSideNavigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_tab_compatible_navigation_and_streaming_status(self) -> None:
        from app.ui.navigation import SideNavigation

        navigation = SideNavigation()
        navigation.addTab("Файлы")
        navigation.addTab("Предпросмотр")
        changed: list[int] = []
        navigation.currentChanged.connect(changed.append)
        navigation.setCurrentIndex(1)
        navigation.set_streaming_state(
            True, queued=2, in_flight=True, folder="C:/Inbox"
        )

        self.assertEqual(navigation.currentIndex(), 1)
        self.assertEqual(changed, [1])
        self.assertIn("активен", navigation.status_title.text())
        self.assertIn("2", navigation.status_detail.text())
        navigation.deleteLater()

    def test_log_clear_requires_explicit_confirmation(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from app.ui.log_view import LogView

        with mock.patch("app.ui.log_view.config.get_log_rows", return_value=[]):
            view = LogView()
        with mock.patch(
            "app.ui.log_view.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Cancel,
        ), mock.patch("app.ui.log_view.config.clear_log") as clear_log:
            view._on_clear()
            clear_log.assert_not_called()

        with mock.patch(
            "app.ui.log_view.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), mock.patch("app.ui.log_view.config.clear_log") as clear_log, mock.patch.object(
            view, "refresh"
        ):
            view._on_clear()
            clear_log.assert_called_once_with()
        view.deleteLater()


if __name__ == "__main__":
    unittest.main()
