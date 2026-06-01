"""
Модуль настройки логирования: loguru → stdout + Logstash TCP (JSON).

При старте приложения вызывается setup_logging(), которая:
1. Сохраняет стандартный stdout-sink (для docker logs).
2. Добавляет TCP-sink, отправляющий JSON-строки на Logstash.
3. Если Logstash недоступен — бот продолжает работать, ошибка логируется в stdout.
"""

import json
import socket
import sys
from datetime import datetime, timezone

from loguru import logger

from config.settings import settings


def _serialize_record(message) -> str:
    """Сериализует loguru-запись в JSON-строку для Logstash."""
    record = message.record
    extra = {k: v for k, v in record["extra"].items()}

    log_entry = {
        "timestamp": record["time"].astimezone(timezone.utc).isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
        "extra": extra,
    }
    return json.dumps(log_entry, ensure_ascii=False, default=str) + "\n"


class _LogstashTCPSink:
    """
    Loguru-совместимый sink: отправляет каждую лог-запись как JSON-строку
    по TCP-соединению на Logstash.

    Соединение создаётся при первой попытке записи и пересоздаётся при обрыве.
    Сетевые ошибки обрабатываются без прерывания работы основного приложения.
    """

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None

    # --- Управление соединением ---

    def _connect(self) -> bool:
        """Попытка установить TCP-соединение с Logstash."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5.0)
            self._sock.connect((self._host, self._port))
            return True
        except OSError as exc:
            # Не удалось подключиться — закрываем сокет, пишем в stderr
            self._close()
            sys.stderr.write(
                f"[log_config] Не удалось подключиться к Logstash "
                f"({self._host}:{self._port}): {exc}\n"
            )
            return False

    def _close(self):
        """Безопасно закрывает TCP-сокет."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # --- Sink-интерфейс для loguru ---

    def write(self, message):
        """
        Вызывается loguru для каждой лог-записи.
        Сериализует запись в JSON и отправляет по TCP.
        """
        payload = _serialize_record(message)

        # Ленивое подключение / реконнект
        if self._sock is None:
            if not self._connect():
                return  # Logstash недоступен — пропускаем запись

        try:
            self._sock.sendall(payload.encode("utf-8"))
        except OSError:
            # Соединение оборвалось — пробуем один реконнект
            self._close()
            if self._connect():
                try:
                    self._sock.sendall(payload.encode("utf-8"))
                except OSError:
                    self._close()


def setup_logging() -> None:
    """
    Инициализация логирования для всего приложения.

    Должна вызываться ОДИН раз при старте (например, в lifespan FastAPI).
    - Удаляет дефолтный sink loguru.
    - Добавляет stdout-sink (для docker logs).
    - Добавляет TCP-sink для Logstash (JSON).
    """
    # Удаляем дефолтный sink, чтобы избежать дубликатов
    logger.remove()

    # 1. stdout-sink — вывод в консоль для docker logs
    logger.add(
        sys.stdout,
        level="DEBUG",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "{message}"
        ),
        colorize=True,
    )

    # 2. TCP-sink → Logstash (JSON)
    logstash_sink = _LogstashTCPSink(
        host=settings.logstash_host,
        port=settings.logstash_port,
    )
    logger.add(
        logstash_sink,
        level="DEBUG",
        format="{message}",  # формат не используется — сериализация в write()
        serialize=False,
    )

    logger.info(
        "Логирование инициализировано: stdout + Logstash TCP "
        f"({settings.logstash_host}:{settings.logstash_port})"
    )
