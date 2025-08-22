# Recepciones — Base (Flask + psycopg v3)

Proyecto base para portal de recepciones.

## Requisitos
- Python 3.11
- PowerShell con ejecución de scripts habilitada (RemoteSigned)

## Arranque local
1. Crear venv y activar.
2. `pip install -r requirements.txt`
3. Copiar `.env.example` a `.env` y llenar variables.
4. `python app.py`
5. Prueba:
   - http://127.0.0.1:5000/health
   - http://127.0.0.1:5000/db/ping

## Despliegue (Render) — más adelante
- Build: `pip install -r requirements.txt`
- Start: `gunicorn -w 2 -k gthread -b 0.0.0.0:10000 wsgi:app`
