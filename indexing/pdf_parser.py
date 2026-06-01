import re
import pymupdf4llm
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter
from loguru import logger

class PDFParser:
    def __init__(self):
        # Fallback-сплиттер для фрагментов, превышающих лимит токенов после семантического разбиения
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1500,
            chunk_overlap=200,
            separators=["\n\n", "\n", "|", ".", " "],
            keep_separator=True
        )
        
        # Семантический сплиттер на базе Markdown-заголовков
        self.headers_to_split_on = [
            ("#", "Header 1"),
            ("##", "Header 2"),
            ("###", "Header 3"),
            ("####", "Header 4"),
        ]
        self.markdown_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=self.headers_to_split_on, 
            strip_headers=False
        )

    def clean_text(self, text: str) -> str:
        """Санитизация текста: удаление артефактов, колонтитулов и технических маркеров"""
        # Фильтрация маркеров изображений
        text = re.sub(r'\*\*==> picture.*?<==\*\*(?:<br>|\n)*', '', text)
        text = re.sub(r'\*\*-----.*?-----\*\*(?:<br>|\n)*', '', text, flags=re.DOTALL)

        # Удаляем колонтитулы типа СЛЕТ.10001-01 и даты выпуска
        text = re.sub(r'СЛЕТ\.\d{5}-\d{2}.*\n?', '', text)
        text = re.sub(r'Выпуск от [а-яА-Яa-zA-Z]+ \d{4}.*\n?', '', text)

        # Удаляем элементы оглавления (в том числе внутри таблиц)
        text = re.sub(r'(?m)^\|?.*\.{5,}\s*\d+\s*\|?$', '', text)
        
        # Удаляем висячие одиночные цифры (номера страниц)
        text = re.sub(r'(?m)^\s*\d+\s*$', '', text)
        
        # Эвристическая фильтрация ГОСТ-заголовков:
        # Валидный заголовок должен начинаться с цифры, ключевых слов "Таблица"/"Рисунок" или состоять из UPPERCASE символов.
        # Преобразование невалидных заголовков в стандартное полужирное начертание.
        def filter_real_headers(match):
            header_marks = match.group(1) # e.g. "##"
            content = match.group(2)      # e.g. "1.1 О документе"
            content_stripped = content.strip()
            
            # Проверяем структуру
            if re.match(r'^(\d+\s*\.|Таблица\s+\d+|Рисунок\s+\d+|[A-ZА-ЯЁ\s]+$)', content_stripped):
                return f"{header_marks} {content}"
            else:
                return f"**{content}**"

        text = re.sub(r'^(#+)\s*(.*)', filter_real_headers, text, flags=re.MULTILINE)

        # Удаление артефактов пустых таблиц (остатки форматирования pymupdf4llm)
        text = re.sub(r'\|\s+\|\s+\|\n\|---\|---\|\n(?:\|\s+\|\s+\|\n)+', '', text)

        # Нормализация переносов строк
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def parse_and_chunk(self, file_path: str):
        logger.info(f"Парсинг: {file_path}")
        try:
            # Полное извлечение Markdown без постраничного разбиения для сохранения семантического контекста
            raw_markdown = pymupdf4llm.to_markdown(file_path, extract_images=False, page_chunks=False)
            
            clean_md = self.clean_text(raw_markdown)
            if not clean_md:
                return []

            # 1. Семантическое разбиение на базе заголовков
            md_header_splits = self.markdown_splitter.split_text(clean_md)
            
            # 2. Применение fallback-сплиттера для oversize-фрагментов
            final_splits = self.text_splitter.split_documents(md_header_splits)
            
            chunks = []
            for doc in final_splits:
                # Формирование иерархического пути заголовков из метаданных
                header_path = " > ".join(doc.metadata.values())
                content = doc.page_content.strip()
                
                if not content:
                    continue
                
                # Инъекция пути заголовков в тело фрагмента для повышения качества векторного поиска
                if header_path:
                    enriched_text = f"[{header_path}]\n{content}"
                else:
                    enriched_text = content
                    
                chunks.append({
                    "text": enriched_text, 
                    "page": 0 # Значение по умолчанию: постраничная привязка отключена ради семантической целостности
                })
                
            return chunks
        except Exception as e:
            logger.error(f"Ошибка парсера: {e}")
            return []