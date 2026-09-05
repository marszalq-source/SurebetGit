FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

WORKDIR /app

# Instalacja zaleznosci Pythona
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalacja przegladarki Chromium dla silnika STS Playwright
RUN playwright install chromium

# Skopiowanie kodu aplikacji
COPY . .

# Konfiguracja srodowiska dla serwera Render
ENV PYTHONUNBUFFERED=1
ENV PORT=10000
EXPOSE 10000

# Uruchomienie wlasciwego skanera w trybie serwera
CMD ["python", "sts_live_scanner.py", "--server"]
