FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Процесс хаба и все subprocess-модули работают от этого пользователя,
# не от root — так ошибка в её же коде не даёт root внутри контейнера.
RUN useradd -m -u 1500 -s /usr/sbin/nologin nyx

COPY main.py .
COPY core ./core
COPY routes ./routes

RUN chown -R nyx:nyx /app
USER nyx

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
