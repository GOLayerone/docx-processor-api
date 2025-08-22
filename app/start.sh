#!/usr/bin/env bash
set -euo pipefail

# Le démarrage de LibreOffice est temporairement désactivé pour le débogage.
# LO_PROFILE_DIR="/tmp/lo_profile"
# mkdir -p "$LO_PROFILE_DIR"
# 
# # Lancer le listener LibreOffice en arrière-plan avec un profil dédié (plus stable en conteneur)
# # Le socket UNO écoute en local sur le port 2002
# soffice --headless --nologo --nofirststartwizard \
#   -env:UserInstallation=file:///tmp/lo_profile \
#   --accept="socket,host=127.0.0.1,port=2002;urp;" &
# 
# # Attendre activement que le port 2002 soit prêt (jusqu'à ~15s)
# for i in {1..15}; do
#   if (echo > /dev/tcp/127.0.0.1/2002) >/dev/null 2>&1; then
#     echo "LibreOffice listener prêt sur 127.0.0.1:2002"
#     break
#   fi
#   echo "En attente du listener LibreOffice... ($i)"
#   sleep 1
# done

# Démarrer Gunicorn
exec gunicorn -k uvicorn.workers.UvicornWorker -w 2 --threads 2 \
  --timeout 600 --graceful-timeout 600 --keep-alive 120 \
  -b :$PORT main:app
