"""
Конвертер Markdown → Telegram HTML.

Telegram поддерживает ограниченный набор HTML-тегов:
<b>, <i>, <code>, <pre>, <a>, <s>, <u>, <tg-spoiler>.

Этот модуль принимает универсальный Markdown (генерируемый LLM) и
преобразует его в безопасный Telegram HTML.
"""

import re
from typing import List

from loguru import logger

# Максимальная длина одного сообщения Telegram (в символах)
TELEGRAM_MAX_LENGTH = 4096


def _escape_html(text: str) -> str:
    """Экранирование символов, которые могут сломать HTML-разметку Telegram."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _convert_table_to_text(table_lines: List[str]) -> str:
    """
    Конвертирует Markdown-таблицу в текстовое представление.

    Маленькие таблицы (≤5 строк данных) → формат «Заголовок: значение».
    Большие таблицы → выровненное текстовое перечисление.
    """
    # Разбираем строки таблицы
    rows: List[List[str]] = []
    for line in table_lines:
        # Убираем ведущие/замыкающие |
        stripped = line.strip().strip("|")
        cells = [c.strip() for c in stripped.split("|")]
        # Пропускаем строку-разделитель (|---|---|)
        if all(re.match(r"^[-:]+$", c) for c in cells):
            continue
        rows.append(cells)

    if not rows:
        return ""

    headers = rows[0]
    data_rows = rows[1:]

    if not data_rows:
        # Только заголовки, без данных
        return "<b>" + " | ".join(h for h in headers) + "</b>"

    # Формат «ключ: значение» — каждая строка данных отдельным блоком
    result_parts: List[str] = []
    for row in data_rows:
        entry_lines: List[str] = []
        for i, header in enumerate(headers):
            value = row[i] if i < len(row) else ""
            entry_lines.append(f"<b>{header}</b>: {value}")
        result_parts.append("\n".join(entry_lines))

    return "\n\n".join(result_parts)


def markdown_to_telegram_html(text: str) -> str:
    """
    Конвертирует Markdown-текст в Telegram HTML.

    Поддерживает:
    - Заголовки (###, ##, #) → <b>
    - Жирный (**текст**) → <b>
    - Курсив (*текст*) → <i>
    - Инлайн-код (`код`) → <code>
    - Блоки кода (```...```) → <pre>
    - Списки (- пункт, * пункт) → • пункт
    - Нумерованные списки (1. пункт) — оставляем как есть
    - Таблицы → текстовый формат
    - Ссылки [текст](url) → <a href="url">текст</a>
    """
    if not text or not text.strip():
        return ""

    # --- Шаг 0: Очистка сырых HTML-тегов, которые могли прийти от LLM ---
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:div|p)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:ul|ol|li|span|strong|em|h[1-6])[^>]*>", "", text, flags=re.IGNORECASE)

    # --- Шаг 1: Извлекаем блоки кода, чтобы не трогать их содержимое ---
    code_blocks: List[str] = []

    def _replace_code_block(match: re.Match) -> str:
        """Заменяем блок кода на плейсхолдер."""
        lang = match.group(1) or ""
        code = match.group(2)
        # Экранируем HTML внутри блока кода
        escaped_code = _escape_html(code.strip())
        if lang:
            block = f"<pre><code class=\"language-{_escape_html(lang)}\">{escaped_code}</code></pre>"
        else:
            block = f"<pre>{escaped_code}</pre>"
        idx = len(code_blocks)
        code_blocks.append(block)
        return f"\x00CODEBLOCK{idx}\x00"

    # Блоки кода (```) — обрабатываем до всех остальных преобразований
    text = re.sub(
        r"```(\w*)\n(.*?)```",
        _replace_code_block,
        text,
        flags=re.DOTALL,
    )

    # --- Шаг 2: Извлекаем инлайн-код, чтобы не трогать его содержимое ---
    inline_codes: List[str] = []

    def _replace_inline_code(match: re.Match) -> str:
        code = match.group(1)
        escaped_code = _escape_html(code)
        formatted = f"<code>{escaped_code}</code>"
        idx = len(inline_codes)
        inline_codes.append(formatted)
        return f"\x00INLINECODE{idx}\x00"

    text = re.sub(r"`([^`\n]+)`", _replace_inline_code, text)

    # --- Шаг 3: Экранируем HTML-символы в основном тексте ---
    # Но не трогаем плейсхолдеры блоков/инлайн-кода
    parts = re.split(r"(\x00(?:CODEBLOCK|INLINECODE)\d+\x00)", text)
    escaped_parts: List[str] = []
    for part in parts:
        if re.match(r"\x00(?:CODEBLOCK|INLINECODE)\d+\x00", part):
            escaped_parts.append(part)
        else:
            escaped_parts.append(_escape_html(part))
    text = "".join(escaped_parts)

    # --- Шаг 4: Обрабатываем таблицы ---
    lines = text.split("\n")
    result_lines: List[str] = []
    table_buffer: List[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Определяем строку таблицы: начинается и заканчивается на |
        if stripped.startswith("|") and stripped.endswith("|"):
            table_buffer.append(stripped)
        else:
            # Если был буфер таблицы — конвертируем
            if table_buffer:
                result_lines.append(_convert_table_to_text(table_buffer))
                table_buffer = []
            result_lines.append(line)
        i += 1

    # Последняя таблица в конце текста
    if table_buffer:
        result_lines.append(_convert_table_to_text(table_buffer))

    text = "\n".join(result_lines)

    # --- Шаг 5: Markdown → HTML (построчно и инлайн) ---

    # Заголовки: ### Текст, ## Текст, # Текст → <b>Текст</b>
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # Жирный: **текст** → <b>текст</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # Курсив: *текст* → <i>текст</i> (не путать с жирным **)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", text)

    # Ссылки: [текст](url) → <a href="url">текст</a>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # Маркированные списки: - пункт или * пункт → • пункт
    text = re.sub(r"^(\s*)[-*]\s+", r"\1• ", text, flags=re.MULTILINE)

    # Горизонтальные линии (---, ***, ___) → пустая строка
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)

    # --- Шаг 6: Возвращаем блоки и инлайн-код на место ---
    for idx, block in enumerate(code_blocks):
        text = text.replace(f"\x00CODEBLOCK{idx}\x00", block)

    for idx, code in enumerate(inline_codes):
        text = text.replace(f"\x00INLINECODE{idx}\x00", code)

    # --- Шаг 7: Убираем лишние пустые строки (более 2 подряд) ---
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def split_message(text: str) -> List[str]:
    """
    Разбивает длинное сообщение на части, каждая не длиннее TELEGRAM_MAX_LENGTH.

    Разбивка происходит по абзацам (двойной перенос строки).
    Если один абзац длиннее лимита — разбиваем по строкам.
    Если одна строка длиннее лимита — разбиваем по символам.
    """
    if len(text) <= TELEGRAM_MAX_LENGTH:
        return [text]

    parts: List[str] = []
    current_part = ""

    paragraphs = text.split("\n\n")

    for paragraph in paragraphs:
        # Проверяем, поместится ли абзац в текущую часть
        candidate = f"{current_part}\n\n{paragraph}" if current_part else paragraph

        if len(candidate) <= TELEGRAM_MAX_LENGTH:
            current_part = candidate
        else:
            # Текущая часть полная — сохраняем
            if current_part:
                parts.append(current_part.strip())
                current_part = ""

            # Абзац сам по себе слишком длинный — разбиваем по строкам
            if len(paragraph) > TELEGRAM_MAX_LENGTH:
                lines = paragraph.split("\n")
                for line in lines:
                    if len(current_part) + len(line) + 1 <= TELEGRAM_MAX_LENGTH:
                        current_part = f"{current_part}\n{line}" if current_part else line
                    else:
                        if current_part:
                            parts.append(current_part.strip())
                        # Если одна строка длиннее лимита — режем по символам
                        if len(line) > TELEGRAM_MAX_LENGTH:
                            for chunk_start in range(0, len(line), TELEGRAM_MAX_LENGTH):
                                parts.append(line[chunk_start:chunk_start + TELEGRAM_MAX_LENGTH])
                            current_part = ""
                        else:
                            current_part = line
            else:
                current_part = paragraph

    if current_part:
        parts.append(current_part.strip())

    return parts


def format_and_split(text: str) -> List[str]:
    """
    Полный конвейер: конвертация Markdown → Telegram HTML + разбивка на части.

    Основная функция для использования в telegram_bot.py.
    """
    if not text:
        return [""]

    html = markdown_to_telegram_html(text)
    return split_message(html)
