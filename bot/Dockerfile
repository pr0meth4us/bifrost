FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --default-timeout=100 --no-cache-dir -r requirements.txt

# Copy only the bot code (we assume context is set to . in compose)
COPY . .

CMD ["python", "main.py"]