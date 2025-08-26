from fastapi import FastAPI, Request, HTTPException, File, UploadFile, Depends, Form, status, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from docxtpl import DocxTemplate
from typing import Dict, Any, Optional, List
import os
import json
import uuid
import logging
import tempfile
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
import secrets
import re
from google.cloud import firestore
from google.cloud import storage
from google.api_core.exceptions import NotFound
from pypdf import PdfMerger
import io
from contextlib import asynccontextmanager
from scripts.clean_docx_fragments import normalize_docx
import zipfile

# --- Configuration --- 
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")
GCP_DISABLED = os.environ.get("DISABLE_GCP", "0") == "1"
TEMPLATE_BUCKET = None

# --- Initialisation des clients GCP via Lifespan ---
db: Optional[firestore.Client] = None
STORAGE_CLIENT: Optional[storage.Client] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, STORAGE_CLIENT, TEMPLATE_BUCKET
    if GCP_DISABLED:
        logger.warning("Mode GCP désactivé (DISABLE_GCP=1) : Firestore/Storage ne seront pas initialisés.")
        yield
        return
    try:
        db = firestore.Client()
        logger.info("Firestore initialisé avec succès.")
    except Exception as e:
        logger.error(f"Impossible d'initialiser Firestore: {e}")

    try:
        STORAGE_CLIENT = storage.Client()
        TEMPLATE_BUCKET_NAME = os.environ.get('TEMPLATE_BUCKET')
        if TEMPLATE_BUCKET_NAME:
            TEMPLATE_BUCKET = STORAGE_CLIENT.bucket(TEMPLATE_BUCKET_NAME)
            logger.info(f"Bucket de stockage '{TEMPLATE_BUCKET_NAME}' initialisé.")
        else:
            logger.warning("TEMPLATE_BUCKET n'est pas configuré.")
    except Exception as e:
        logger.error(f"Impossible d'initialiser le client de stockage: {e}")

    yield

app = FastAPI(
    title="API de traitement de documents Word",
    description="API pour fusionner des données JSON dans des templates .docx",
    version="1.2.0",
    lifespan=lifespan,
)

PLANS = {"gratuit": 50, "starter": 500, "pro": 5000, "illimite": None}
UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), "docx_processor")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Middlewares et Handlers --- 
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Erreur non gérée: {exc}\n{traceback.format_exc()}")
    return JSONResponse(status_code=500, content={"detail": f"Erreur interne: {exc}"})

# --- Fonctions utilitaires et dépendances ---

def get_api_key_from_request(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Header X-API-Key manquant")
    return api_key

def admin_required(request: Request):
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Authentification admin non configurée")
    token = request.headers.get("X-Admin-Token")
    if not token or not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Token admin invalide")

def _ensure_gcs_ready():
    if not TEMPLATE_BUCKET or not STORAGE_CLIENT:
        raise HTTPException(status_code=503, detail="Service de stockage non configuré")

def check_quota_or_raise(api_key: str):
    if GCP_DISABLED:
        logger.info("Quota ignoré (mode GCP désactivé)")
        return
    if not db: raise HTTPException(status_code=503, detail="DB non disponible")
    doc = db.collection("api_keys").document(api_key).get()
    if not doc.exists: raise HTTPException(status_code=401, detail="Clé API invalide")
    data = doc.to_dict()
    if not data.get("is_active", True): raise HTTPException(status_code=403, detail="Clé API désactivée")
    quota = PLANS.get(data.get("plan", "gratuit"))
    if quota is not None and data.get("quota_used", 0) >= quota:
        raise HTTPException(status_code=429, detail="Quota mensuel atteint")

def _detect_tags(docx_path: str) -> List[str]:
    """Inspecte les parties XML d'un DOCX pour détecter des balises Jinja {{var}}.
    Retourne une liste de noms de variables détectés (unicité conservée).
    Ceci est un best-effort basé sur regex après normalisation.
    """
    try:
        found: List[str] = []
        seen = set()
        with zipfile.ZipFile(docx_path, 'r') as z:
            for item in z.infolist():
                if item.filename.startswith('word/') and item.filename.endswith('.xml'):
                    data = z.read(item.filename)
                    text = data.decode('utf-8', 'ignore')
                    # Cherche {{ ... }} sans espaces obligatoires
                    for m in re.finditer(r"\{\{\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}", text):
                        var = m.group(1)
                        if var not in seen:
                            seen.add(var)
                            found.append(var)
        return found
    except Exception as e:
        logger.warning(f"Detection des balises échouée: {e}")
        return []

@firestore.transactional
def consume_quota_transaction(transaction, key_ref):
    snapshot = key_ref.get(transaction=transaction)
    if not snapshot.exists: raise ValueError("Clé API introuvable")
    new_usage = snapshot.get("quota_used") + 1
    transaction.update(key_ref, {"quota_used": new_usage})

# --- Endpoints Client pour les templates ---

@app.post("/client/templates")
async def create_client_template(request: Request, template: UploadFile = File(...)):
    api_key = get_api_key_from_request(request)
    check_quota_or_raise(api_key)
    _ensure_gcs_ready()
    if not (template.filename or "").lower().endswith('.docx'):
        raise HTTPException(status_code=400, detail="Seuls les .docx sont acceptés")
    data = await template.read()
    if not data: raise HTTPException(status_code=400, detail="Fichier vide")
    template_id = str(uuid.uuid4())
    blob = TEMPLATE_BUCKET.blob(f"{api_key}/{template_id}.docx")
    blob.upload_from_string(data, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    
    # Récupérer le nom du client depuis la base de données
    client_name = "Client inconnu"
    if db:
        try:
            doc = db.collection("api_keys").document(api_key).get()
            if doc.exists:
                client_name = doc.to_dict().get("name", "Client inconnu")
        except Exception:
            pass
    
    return {"template_id": template_id, "status": "created", "client_name": client_name}

@app.get("/client/templates")
async def list_client_templates(request: Request):
    api_key = get_api_key_from_request(request)
    _ensure_gcs_ready()
    blobs = TEMPLATE_BUCKET.list_blobs(prefix=f"{api_key}/")
    # Récupérer le nom du client depuis la base de données
    client_name = "Client inconnu"
    if db:
        try:
            doc = db.collection("api_keys").document(api_key).get()
            if doc.exists:
                client_name = doc.to_dict().get("name", "Client inconnu")
        except Exception:
            pass
    return {
        "client_name": client_name,
        "templates": [{"id": b.name.split('/')[-1][:-5], "size": b.size, "updated": b.updated, "client_name": client_name} for b in blobs]
    }

@app.get("/client/templates/{template_id}")
async def get_client_template(template_id: str, request: Request):
    api_key = get_api_key_from_request(request)
    _ensure_gcs_ready()
    blob = TEMPLATE_BUCKET.blob(f"{api_key}/{template_id}.docx")
    if not blob.exists(): raise HTTPException(status_code=404, detail="Template introuvable")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
        blob.download_to_filename(tmp.name)
        return FileResponse(tmp.name, filename=f"template_{template_id}.docx")

@app.put("/client/templates/{template_id}")
async def replace_client_template(template_id: str, request: Request, template: UploadFile = File(...)):
    api_key = get_api_key_from_request(request)
    _ensure_gcs_ready()
    if not (template.filename or "").lower().endswith('.docx'):
        raise HTTPException(status_code=400, detail="Seuls les .docx sont acceptés")
    data = await template.read()
    if not data: raise HTTPException(status_code=400, detail="Fichier vide")
    blob = TEMPLATE_BUCKET.blob(f"{api_key}/{template_id}.docx")
    if not blob.exists(): raise HTTPException(status_code=404, detail="Template introuvable")
    blob.upload_from_string(data, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    return {"template_id": template_id, "status": "replaced"}

@app.delete("/client/templates/{template_id}")
async def delete_client_template(template_id: str, request: Request):
    api_key = get_api_key_from_request(request)
    _ensure_gcs_ready()
    blob = TEMPLATE_BUCKET.blob(f"{api_key}/{template_id}.docx")
    if not blob.exists(): raise HTTPException(status_code=404, detail="Template introuvable")
    blob.delete()
    return {"template_id": template_id, "status": "deleted"}

# --- Endpoints Admin Manager ---

@app.post("/admin/templates-manager")
async def templates_manager(
    req: Request,
    action: str = Form(...),
    client_api_key: str = Form(None),
    template_id: str = Form(None),
    template: UploadFile = File(None),
    full_path: str = Form(None),
):
    admin_required(req)
    _ensure_gcs_ready()
    bucket = TEMPLATE_BUCKET
    if action == "list_all":
        # Construire une liste (pas un dict) afin d'éviter les collisions par template_id
        templates_list = []
        for b in bucket.list_blobs():
            # Extraire l'api_key du chemin du blob (format: api_key/template_id.docx)
            path_parts = b.name.split('/')
            api_key_from_path = path_parts[0] if len(path_parts) >= 1 else "Inconnu"
            t_id = path_parts[1][:-5] if len(path_parts) >= 2 and path_parts[1].lower().endswith('.docx') else "Inconnu"
            client_name = "Client inconnu"
            if db and api_key_from_path not in (None, "Inconnu"):
                try:
                    doc = db.collection("api_keys").document(api_key_from_path).get()
                    if doc.exists:
                        client_name = doc.to_dict().get("name", "Client inconnu")
                except Exception:
                    pass
            templates_list.append({
                "template_id": t_id,
                "api_key": api_key_from_path,
                "client_name": client_name,
                "size": b.size,
                "updated": b.updated,
                "full_path": b.name,
            })
        # Trier par nom de client puis par template_id pour stabilité
        templates_list.sort(key=lambda x: (x.get("client_name") or "", x.get("template_id") or ""))
        return {"templates": templates_list}
    if action == "delete":
        # Suppression ciblée par chemin exact si fourni
        if full_path:
            blob = bucket.blob(full_path)
            if not blob.exists():
                raise HTTPException(404, "Template introuvable (full_path)")
            blob.delete()
            return {"status": "deleted", "full_path": full_path}
        # Sinon fallback: suppression par template_id (supprime le premier trouvé)
        if not template_id:
            raise HTTPException(400, "'template_id' requis si 'full_path' n'est pas fourni")
        found_blob = None
        for blob in bucket.list_blobs():
            if blob.name.endswith(f"/{template_id}.docx"):
                found_blob = blob
                break
        if not found_blob:
            raise HTTPException(404, "Template introuvable (template_id)")
        found_blob.delete()
        return {"status": "deleted", "template_id": template_id, "full_path": found_blob.name}
    
    # Pour toutes les autres actions, client_api_key est requis
    if not client_api_key: raise HTTPException(status_code=400, detail="'client_api_key' requis")
    if action == "list_client":
        blobs = bucket.list_blobs(prefix=f"{client_api_key}/")
        # Récupérer le nom du client
        client_name = "Client inconnu"
        if db:
            try:
                doc = db.collection("api_keys").document(client_api_key).get()
                if doc.exists:
                    client_name = doc.to_dict().get("name", "Client inconnu")
            except Exception:
                pass
        return {
            "client_name": client_name,
            "templates": [{"id": b.name.split('/')[-1][:-5], "size": b.size} for b in blobs]
        }
    if not template_id and action in ["replace"]: raise HTTPException(400, "'template_id' requis")
    gcs_path = f"{client_api_key}/{template_id}.docx"
    blob = bucket.blob(gcs_path)
    if not template: raise HTTPException(400, "Fichier 'template' requis")
    data = await template.read()
    if not data: raise HTTPException(400, "Fichier vide")
    if action == "upload":
        new_id = str(uuid.uuid4())
        blob = bucket.blob(f"{client_api_key}/{new_id}.docx")
        blob.upload_from_string(data)
        return {"status": "created", "template_id": new_id}
    if action == "replace":
        if not blob.exists(): raise HTTPException(404, "Template introuvable")
        blob.upload_from_string(data)
        return {"status": "replaced"}
    raise HTTPException(400, f"Action '{action}' non valide")

@app.post("/admin/clients-manager")
async def clients_manager(req: Request, action: str=Form(...), client_api_key: str=Form(None), plan: str=Form(None), is_active: bool=Form(None), name: str=Form(None)):
    admin_required(req)
    if not db: raise HTTPException(503, "DB non disponible")
    if action == "create_key":
        if not name: raise HTTPException(400, "'name' requis")
        new_key = f"sk_{uuid.uuid4().hex}"
        data = {"name": name, "plan": plan or "gratuit", "quota_used": 0, "is_active": True, "created_at": datetime.now(timezone.utc)}
        db.collection("api_keys").document(new_key).set(data)
        return {"api_key": new_key, "details": data}
    if action == "list_keys":
        keys_data = {}
        for d in db.collection("api_keys").stream():
            data = d.to_dict()
            keys_data[d.id] = {
                "name": data.get("name", "Client inconnu"),
                "plan": data.get("plan", "gratuit"),
                "quota_used": data.get("quota_used", 0),
                "is_active": data.get("is_active", True),
                "created_at": data.get("created_at")
            }
        return {"keys": keys_data}
    if not client_api_key: raise HTTPException(400, "'client_api_key' requis")
    doc_ref = db.collection("api_keys").document(client_api_key)
    if action == "get_details":
        doc = doc_ref.get()
        if not doc.exists: raise HTTPException(404, "Clé API introuvable")
        return {"details": doc.to_dict()}
    if action == "update_key":
        update = {k:v for k,v in { 'plan': plan, 'is_active': is_active}.items() if v is not None}
        if not update: raise HTTPException(400, "Aucun champ à modifier")
        doc_ref.update(update)
        return {"status": "updated"}
    if action == "delete_key":
        doc_ref.delete()
        return {"status": "deleted"}
    if action == "reset_quota":
        doc_ref.update({"quota_used": 0})
        return {"status": "quota_reset"}
    raise HTTPException(400, f"Action '{action}' non valide")

@app.post("/admin/system-manager")
async def system_manager(req: Request, action: str=Form(...)):
    admin_required(req)
    if action == "get_stats":
        keys = len(list(db.collection('api_keys').stream())) if db else 'N/A'
        templates = len(list(TEMPLATE_BUCKET.list_blobs())) if TEMPLATE_BUCKET else 'N/A'
        return {"stats": {"api_keys": keys, "templates": templates}}
    if action == "health_check":
        return {"services": {"gcs": "OK" if STORAGE_CLIENT else "Error", "firestore": "OK" if db else "Error"}}
    if action == "get_config":
        bucket_name = getattr(TEMPLATE_BUCKET, 'name', None) if TEMPLATE_BUCKET else None
        return {"config": {"bucket": bucket_name, "admin_token_set": bool(ADMIN_TOKEN)}}
    raise HTTPException(400, f"Action '{action}' non valide")

@app.post("/process-document")
async def process_document(
    req: Request,
    background_tasks: BackgroundTasks,
    template_id: str = Form(None),
    json_data: str = Form(...),
    output_format: str = Form("docx"),
    template: UploadFile = File(None),
    output_filename: str = Form(None),
):
    api_key = get_api_key_from_request(req)
    check_quota_or_raise(api_key)
    if not template_id and not template: raise HTTPException(400, "'template_id' ou 'template' requis")
    session_dir = os.path.join(UPLOAD_FOLDER, str(uuid.uuid4()))
    os.makedirs(session_dir)
    try:
        if template_id:
            file_path = os.path.join(session_dir, f"{template_id}.docx")
            _ensure_gcs_ready()
            TEMPLATE_BUCKET.blob(f"{api_key}/{template_id}.docx").download_to_filename(file_path)
        else:
            file_path = os.path.join(session_dir, template.filename)
            with open(file_path, "wb") as buffer: shutil.copyfileobj(template.file, buffer)
        # Normaliser les balises potentiellement fragmentées avant rendu
        normalized_path = os.path.join(session_dir, "normalized.docx")
        try:
            _ = normalize_docx(file_path, normalized_path, enable_square=False)
            effective_path = normalized_path if os.path.exists(normalized_path) else file_path
        except Exception as e:
            logger.warning(f"Normalisation des balises échouée, utilisation du fichier original: {e}")
            effective_path = file_path

        # Détection (logging) des balises présentes
        detected = _detect_tags(effective_path)
        if detected:
            logger.info(f"Balises détectées dans le template: {detected}")
        else:
            logger.info("Aucune balise {{...}} détectée dans le template après normalisation.")

        context = json.loads(json_data)
        doc = DocxTemplate(effective_path)
        doc.render(context)
        output_docx = os.path.join(session_dir, "output.docx")
        doc.save(output_docx)
        if db: consume_quota_transaction(db.transaction(), db.collection("api_keys").document(api_key))

        def _build_filename(requested: Optional[str], ext: str, default_name: str) -> str:
            name = (requested or default_name).strip()
            # retirer tout chemin
            name = os.path.basename(name)
            # forcer extension
            if not name.lower().endswith(ext):
                name = os.path.splitext(name)[0] + ext
            # sanitation basique: autoriser lettres/chiffres/._-
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
            if not name or name in (".", ".."):
                name = default_name
            return name

        if output_format.lower() == 'pdf':
            try:
                logger.info(f"Conversion de {output_docx} en PDF via LibreOffice headless...")
                # Utilise LibreOffice en mode headless (plus fiable sur Cloud Run)
                cmd = [
                    "libreoffice",
                    "--headless", "--nologo", "--nolockcheck", "--nodefault", "--invisible",
                    "--convert-to", "pdf",
                    "--outdir", session_dir,
                    output_docx,
                ]
                result = subprocess.run(
                    cmd,
                    check=True,
                    timeout=120,
                    capture_output=True
                )
                if result.stdout:
                    logger.info(f"LibreOffice stdout: {result.stdout.decode('utf-8', 'ignore')}")
                if result.stderr:
                    logger.info(f"LibreOffice stderr: {result.stderr.decode('utf-8', 'ignore')}")

                output_pdf = os.path.splitext(output_docx)[0] + ".pdf"
                if not os.path.exists(output_pdf):
                    raise RuntimeError("Le fichier PDF de sortie n'a pas été trouvé après la conversion.")
                
                final_name = _build_filename(output_filename, ".pdf", "result.pdf")
                background_tasks.add_task(shutil.rmtree, session_dir)
                return FileResponse(output_pdf, media_type='application/pdf', filename=final_name)

            except FileNotFoundError:
                logger.error("La commande 'libreoffice' est introuvable. LibreOffice est-il installé dans l'image ?")
                raise HTTPException(status_code=501, detail="La conversion PDF n'est pas disponible sur le serveur (libreoffice manquant).")
            except subprocess.TimeoutExpired:
                logger.error("La conversion PDF a expiré (timeout).")
                raise HTTPException(status_code=500, detail="La conversion PDF a pris trop de temps.")
            except subprocess.CalledProcessError as e:
                logger.error(f"Erreur de conversion PDF avec LibreOffice. Stderr: {e.stderr.decode('utf-8', 'ignore')}")
                raise HTTPException(status_code=500, detail="Erreur lors de la conversion en PDF.")
            except Exception as e:
                logger.error(f"Erreur inattendue lors de la conversion PDF: {e}")
                raise HTTPException(status_code=500, detail=f"Erreur inattendue lors de la conversion PDF.")

        # Retourner DOCX par défaut
        final_name = _build_filename(output_filename, ".docx", "result.docx")
        background_tasks.add_task(shutil.rmtree, session_dir)
        return FileResponse(output_docx, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document', filename=final_name)
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        logger.error(f"Erreur traitement document: {e}")
        raise HTTPException(500, f"Erreur interne: {e}")

@app.post("/merge-pdf")
async def merge_pdf(
    req: Request,
    background_tasks: BackgroundTasks,
    pdf_files: List[UploadFile] = File(None),
    # Accept common alternates used by some clients (e.g., Bubble):
    pdf_files_brackets: List[UploadFile] = File(None, alias="pdf_files[]"),
    file_list_alt1: List[UploadFile] = File(None, alias="file"),
    file_list_alt2: List[UploadFile] = File(None, alias="files"),
    output_filename: str = Form(None),
):
    """Fusionne plusieurs fichiers PDF en un seul.
    - Auth: header X-API-Key
    - Input: form-data avec champ 'pdf_files' (plusieurs fichiers PDF)
    - Output: PDF fusionné
    """
    api_key = get_api_key_from_request(req)
    check_quota_or_raise(api_key)
    # Merge possible inputs into a single list
    merged_inputs: List[UploadFile] = []
    for group in (pdf_files, pdf_files_brackets, file_list_alt1, file_list_alt2):
        if group:
            merged_inputs.extend(group)

    if not merged_inputs or len(merged_inputs) < 2:
        raise HTTPException(status_code=400, detail="Fournissez au moins deux fichiers PDF")

    session_dir = os.path.join(UPLOAD_FOLDER, str(uuid.uuid4()))
    os.makedirs(session_dir)
    try:
        merger = PdfMerger()
        temp_paths: List[str] = []

        for f in merged_inputs:
            name = (f.filename or "").lower()
            if not name.endswith(".pdf"):
                raise HTTPException(status_code=400, detail=f"Fichier non PDF détecté: {f.filename}")
            data = await f.read()
            if not data:
                raise HTTPException(status_code=400, detail=f"Fichier vide: {f.filename}")
            # Utiliser un buffer mémoire pour éviter les corruptions
            buffer = io.BytesIO(data)
            try:
                merger.append(buffer)
            except Exception:
                # fallback: écrire temporairement si nécessaire
                tmp_path = os.path.join(session_dir, f"{uuid.uuid4().hex}.pdf")
                with open(tmp_path, "wb") as tmpf:
                    tmpf.write(data)
                temp_paths.append(tmp_path)
                merger.append(tmp_path)

        # Écrire le PDF fusionné
        output_pdf = os.path.join(session_dir, "merged.pdf")
        with open(output_pdf, "wb") as out:
            merger.write(out)
        merger.close()

        if db:
            consume_quota_transaction(db.transaction(), db.collection("api_keys").document(api_key))

        # Déterminer le nom de fichier final
        def _sanitize_filename(requested: Optional[str]) -> str:
            name = (requested or "merged.pdf").strip()
            name = os.path.basename(name)
            if not name.lower().endswith(".pdf"):
                name = os.path.splitext(name)[0] + ".pdf"
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
            if not name or name in (".", ".."):
                name = "merged.pdf"
            return name

        final_name = _sanitize_filename(output_filename)
        background_tasks.add_task(shutil.rmtree, session_dir)
        return FileResponse(output_pdf, media_type='application/pdf', filename=final_name)
    except HTTPException:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        logger.error(f"Erreur lors de la fusion PDF: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la fusion des PDF")

@app.get("/")
async def root():
    logger.info("Request received at root endpoint (/)")
    return {"message": "Service de traitement de documents DOCX avec remplacement de balises"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8008)))
