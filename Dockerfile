FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

# Zainstaluj zaleznosci Pythona
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Skopiuj pliki projektu
COPY . .

# Zmienna portu dla Render.com
ENV PORT=10000
EXPOSE 10000

# Uruchomienie skanera w trybie headless server
CMD ["python", "sts_live_scanner.py", "--server"]
