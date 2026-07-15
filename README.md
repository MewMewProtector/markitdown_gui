# markitdown_gui

**Версия: v0.0.1**

Десктоп-приложение GUI-обёрткой над `markitdown[all]`.
Следит за выбранной папкой, подсвечивает новые поддерживаемые файлы,
позволяет конвертировать их в `.md` и просматривать результат с подсветкой
синтаксиса Markdown.

## Возможности

- Слежение за выбранной папкой (`QFileSystemWatcher`, debounce 500 мс).
- Drag-n-drop файлов и папок прямо в окно.
- Ручное добавление через диалог (фильтр по поддерживаемым расширениям).
- Конвертация выбранных файлов в пуле потоков (`QThreadPool` + `QRunnable`).
- Предпросмотр с подсветкой Markdown (`QSyntaxHighlighter`).
- Тёмная / светлая темы, фреймлесс-окно с кастомным заголовком.
- Журнал конвертаций в SQLite + экспорт CSV.
- Настройки MarkItDown: LLM (OpenAI-совместимый), Document Intelligence,
  Content Understanding, плагины, аудио/YouTube.

## Требования

- Python **3.10+**
- Windows 10 / 11 (для сборки `.exe`)

## Установка

```bash
pip install -r requirements.txt
```

или через `pyproject.toml`:

```bash
pip install -e .
```

## Запуск

```bash
python -m app
```

или через установленный CLI:

```bash
markitdown-gui
```

## Использование

1. Откройте вкладку **«Настройки»**, выберите **папку сканирования** и
   (опционально) **папку сохранения**. Если папка сохранения пуста — `.md`
   файлы сохраняются рядом с исходниками.
2. Сохраните настройки. Приложение начнёт следить за выбранной папкой.
3. На вкладке **«Файлы»** появятся новые поддерживаемые файлы с галочками.
   Двойной клик по строке — переход к предпросмотру уже сконвертированного
   `.md` (если он есть).
4. Отметьте нужные файлы и нажмите **«Преобразовать выбранные»**.
5. Прогресс и итоги — на вкладке **«Лог»**.

### Поддерживаемые форматы

PDF, DOC/DOCX, PPT/PPTX, XLS/XLSX, HTML/HTM, MD, CSV, JSON, XML, TXT,
JPG/JPEG/PNG/GIF/BMP/WEBP/TIFF, MP3/WAV/M4A, EPUB, ZIP, MSG/EML,
а также ссылки `http(s)://`.

## Сборка `.exe`

Только локально, **не пушим** `dist/` и `build/`:

```bash
pip install pyinstaller
pyinstaller --noconfirm packaging/markitdown_gui.spec
```

Артефакт — `dist/MarkItDownGUI.exe`.

## Структура проекта

```
markitdown_gui/
├── pyproject.toml
├── requirements.txt
├── app/
│   ├── __main__.py          # точка входа: python -m app
│   ├── main_window.py       # QMainWindow, фреймлесс, drag-n-drop
│   ├── ui/                  # title_bar, theme, toast, scan/preview/settings/log
│   └── core/                # config (SQLite), watcher, converter, jobs
└── packaging/
    └── markitdown_gui.spec  # PyInstaller
```

## Лицензия

MIT.