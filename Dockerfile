# Базовый образ с Python. pandoc ставим через apt (системная программа,
# не Python-пакет) — без неё docx_parser.py не сможет конвертировать .docx.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends pandoc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Платформы деплоя (Render, Railway и т.п.) сами подставляют переменную
# PORT — слушаем именно её, а не фиксированный порт.
ENV PORT=8000
EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
