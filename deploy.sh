#!/bin/bash

# Exit on error
set -e

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "Error: Google Cloud SDK (gcloud) is not installed."
    echo "Please install it from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check if user is authenticated with gcloud
if ! gcloud auth list --filter=status:ACTIVE --format='value(account)' &> /dev/null; then
    echo "You need to authenticate with Google Cloud first."
    echo "Running: gcloud auth login"
    gcloud auth login
fi

# Get project ID
PROJECT_ID=$(gcloud config get-value project)
if [ -z "$PROJECT_ID" ]; then
    echo "No project ID is set. Please set a project ID first:"
    echo "gcloud config set project YOUR_PROJECT_ID"
    exit 1
fi

# Enable required APIs
echo "Enabling required APIs..."
gcloud services enable run.googleapis.com

gcloud services enable cloudbuild.googleapis.com
gcloud services enable containerregistry.googleapis.com

# Set the service name
SERVICE_NAME="docx-processor"
REGION="us-central1"  # Change this to your preferred region

# Build and push the Docker image
echo "Building and pushing Docker image..."
gcloud builds submit --tag gcr.io/${PROJECT_ID}/${SERVICE_NAME}

# Deploy to Cloud Run
echo "Deploying to Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
  --image gcr.io/${PROJECT_ID}/${SERVICE_NAME} \
  --platform managed \
  --region ${REGION} \
  --allow-unauthenticated \
  --memory 2Gi \
  --timeout 900s \
  --port 8080 \
  --set-env-vars=PYTHONUNBUFFERED=1

# Get the service URL
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --platform managed --region ${REGION} --format 'value(status.url)')

echo "Deployment complete!"
echo "Service URL: ${SERVICE_URL}"
echo "API Documentation: ${SERVICE_URL}/docs"
