FROM python:3.11-slim

WORKDIR /app

# 1. Install dependencies
COPY requirements.txt .
RUN pip install --default-timeout=100 --no-cache-dir -r requirements.txt

# 2. Copy the entire Bifrost project (API + Bot code)
COPY . .

# 3. Make the startup script executable
COPY run.sh .
RUN chmod +x run.sh

# 4. Expose the API port (Koyeb usually expects 8000)
EXPOSE 8000

# 5. Run the combined script
CMD ["./run.sh"]