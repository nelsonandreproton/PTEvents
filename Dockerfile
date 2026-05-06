FROM python:3.11-slim

RUN useradd -u 1000 -m appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data && chown -R appuser:appuser /app

USER appuser

CMD ["python", "bot/main.py"]
