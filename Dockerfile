FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Предварительная установка PyTorch (CPU-версия) для колоссальной экономии места (~2.5 ГБ) и ускорения сборки
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# Установка тяжелых зависимостей для кэширования слоя
RUN pip install --no-cache-dir sentence-transformers==2.5.1 pymupdf4llm>=0.0.17

# Копируем и устанавливаем зависимости (кэшируется Docker'ом)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Предзагрузка embedding-модели (чтобы не качать при каждом старте)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

# Копируем исходный код проекта
COPY . .

# Проброс порта
EXPOSE 8000

# Запуск приложения
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
