import sys
import json
import socket
from loguru import logger
from config.settings import settings

def logstash_sink(message):
    """
    Простой sink для отправки логов по TCP в Logstash.
    Парсит сообщение loguru и создает JSON payload.
    """
    record = message.record
    log_data = {
        "@timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
        "node": getattr(record.get("extra", {}), "node_name", "system")
    }
    
    # Добавляем любые дополнительные свойства, переданные в логгер
    if record["extra"]:
        for key, value in record["extra"].items():
            log_data[key] = value

    try:
        payload = json.dumps(log_data) + "\n"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect((settings.logstash_host, settings.logstash_port))
            sock.sendall(payload.encode('utf-8'))
    except Exception as e:
        # Fallback на стандартный вывод ошибок, если Logstash недоступен
        # Прямой вызов logger.error может вызвать бесконечный цикл, поэтому используем print
        print(f"Не удалось отправить лог в Logstash: {e}", file=sys.stderr)

def setup_logging():
    """
    Настраивает логгер Loguru для вывода в консоль и удаленной отправки в Logstash.
    """
    # Удаляем обработчик по умолчанию
    logger.remove()
    
    # Обработчик консоли для локальной разработки (цветной)
    logger.add(
        sys.stdout, 
        colorize=True, 
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO"
    )
    
    # Обработчик Logstash (TCP)
    # Добавляется только если logstash_host не 'localhost' или если мы специально этого хотим
    if settings.logstash_host:
        logger.add(
            logstash_sink,
            level="DEBUG",
            serialize=True
        )

# Инициализация при импорте
setup_logging()
