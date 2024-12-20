# Scegliamo un'immagine base
FROM python:3.9-slim

# Impostiamo la directory di lavoro nel container
WORKDIR /app

# Copiamo il file requirements.txt nel container
COPY requirements.txt .

# Installiamo le dipendenze Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiamo i file necessari nel container
COPY . .
# Espone la porta 8003 per FastAPI
EXPOSE 8003

# Comando per avviare l'applicazione FastAPI
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]