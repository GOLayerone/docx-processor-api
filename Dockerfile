# Utiliser l'image Python 3.9 officielle
FROM python:3.9-slim

# Installer les dépendances système
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -yq --no-install-recommends \
    libreoffice \
 && rm -rf /var/lib/apt/lists/*

# Définir les variables d'environnement
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Définir le répertoire de travail
WORKDIR /app

# Copier le fichier de dépendances
COPY requirements.txt .

# Installer les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code de l'application
COPY . .

# Créer un utilisateur non root et basculer dessus
RUN useradd -m myuser && chown -R myuser:myuser /app
USER myuser

# Commande pour exécuter l'application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-keep-alive", "300"]
