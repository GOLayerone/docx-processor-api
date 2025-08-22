# Docx Template Processor API

A FastAPI application that processes Word documents with Jinja2 templates and converts them to PDF. Deployed on Google Cloud Run for scalability and reliability.

## Features

- Process Word (.docx) documents with Jinja2 templates
- Convert processed documents to PDF
- RESTful API with OpenAPI documentation
- Containerized with Docker
- Scalable deployment on Google Cloud Run

## Local Development

### Prerequisites

- Python 3.9+
- Docker (for containerized development)
- Google Cloud SDK (for deployment)

### Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd docx-template-processor
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Running Locally

1. Start the application:
   ```bash
   uvicorn app.main:app --reload
   ```

2. Access the API documentation at http://localhost:8000/docs

### Building with Docker

1. Build the Docker image:
   ```bash
   docker build -t docx-processor .
   ```

2. Run the container:
   ```bash
   docker run -p 8080:8080 docx-processor
   ```

## Deployment to Google Cloud Run

### Prerequisites

1. Google Cloud account with billing enabled
2. Google Cloud SDK installed and configured
3. Docker installed
4. Project created in Google Cloud Console

### Deployment Steps

1. **Authenticate with Google Cloud:**
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   gcloud auth configure-docker
   ```

2. **Build and push the Docker image:**
   ```bash
   gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/docx-processor
   ```

3. **Deploy to Cloud Run:**
   ```bash
   gcloud run deploy docx-processor \
     --image gcr.io/YOUR_PROJECT_ID/docx-processor \
     --platform managed \
     --region us-central1 \
     --allow-unauthenticated \
     --memory 2Gi \
     --timeout 900s \
     --port 8080
   ```

4. **After deployment**, you'll receive a service URL. Access the API documentation at `{SERVICE_URL}/docs`

## API Documentation

Once deployed, access the interactive API documentation at:
- `/docs` - Swagger UI
- `/redoc` - ReDoc UI

## Environment Variables

- `PORT`: Port the application listens on (default: 8080)
- `PYTHONUNBUFFERED`: Set to 1 for non-buffered logging (recommended for containerized environments)

## License

MIT

## Utilisation

1. Démarrer le serveur :
   ```bash
   uvicorn app.main:app --reload
   ```

2. L'application sera disponible sur : `http://localhost:8000`

## API Endpoints

### Traiter un document

**POST** `/process-document`

Paramètres :
- `template`: Fichier Word (.docx) contenant les balises
- `json_data`: Chaîne JSON contenant les valeurs de remplacement

Exemple de requête avec cURL :
```bash
curl -X 'POST' \
  'http://localhost:8000/process-document' \
  -H 'accept: application/json' \
  -F 'template=@document_avec_balises.docx' \
  -F 'json_data={"nom":"Dupont", "prenom":"Jean"}'
```

## Utilisation avec Bubble

1. Dans votre application Bubble, créez un workflow qui :
   - Récupère le fichier DOCX (via un upload ou depuis une base de données)
   - Prépare les données de remplacement au format JSON
   - Envoie une requête POST à votre endpoint avec les fichiers et données
   - Récupère et affiche ou télécharge le PDF généré

## Format des balises dans le document Word

Utilisez la syntaxe `{{nom_variable}}` dans votre document Word. Par exemple :

```
Cher {{civilite}} {{nom}} {{prenom}},

Nous vous remercions pour votre commande n°{{numero_commande}}.
```

## Déploiement

Pour une utilisation en production, il est recommandé de :
1. Déployer l'application sur un service comme Heroku, Google Cloud Run ou AWS Lambda
2. Configurer un domaine personnalisé
3. Mettre en place une authentification si nécessaire
4. Configurer HTTPS
5. Mettre en place un système de nettoyage des fichiers temporaires
