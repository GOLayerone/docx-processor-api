import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Importer l'application et le client de test
from main import app
from fastapi.testclient import TestClient

# Créer un client de test pour l'application
client = TestClient(app)


def test_root_endpoint():
    """Teste que l'endpoint racine est fonctionnel."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Service de traitement de documents DOCX avec remplacement de balises"}


@patch('main.db')
def test_process_document_requires_auth(mock_db):
    """Vérifie que l'endpoint de traitement nécessite une clé API."""
    # Simuler une base de données non disponible pour ce test
    mock_db.collection.return_value.document.return_value.get.side_effect = Exception("DB not available")
    response = client.post("/process-document", data={"json_data": '{}'}, files={'template': ('t.docx', b'c')})
    assert response.status_code == 401  # X-API-Key manquant


@patch('main.db')
def test_process_document_invalid_key(mock_db):
    """Vérifie la gestion d'une clé API invalide."""
    # Configurer le mock pour simuler une clé API inexistante
    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    response = client.post(
        "/process-document",
        data={"json_data": '{"name": "Test"}'},
        files={'template': ('template.docx', b'content')},
        headers={"X-API-Key": "invalid-key"}
    )
    assert response.status_code == 401
    assert "Clé API invalide" in response.text
