# 📡 Restock Radar

Monitor automatizado de tiendas online que detecta lanzamientos, restocks y productos exclusivos, y envía notificaciones en tiempo real.

> 🚧 En desarrollo. Primera tienda soportada: **Cold Culture** (Shopify).

## ¿Qué hace?

- **Monitorea tiendas** usando sus endpoints públicos (p. ej. `products.json` de Shopify), sin scraping agresivo.
- **Detecta cambios**: productos nuevos, vuelta de stock, cambios de precio y tallas agotadas.
- **Clasifica exclusividad** ("Vault", "Limited", acabados especiales) con reglas y la API de Claude.
- **Analiza patrones de restock** a partir del histórico de stock para estimar cuándo puede volver un producto.
- **Notifica** mediante Firebase Cloud Messaging y muestra todo en un dashboard web.

## Arquitectura

GitHub Actions (cron)
  └─ Monitor (Python)
       ├─ Adaptadores por tienda (Shopify, HTML…)
       ├─ Detector de cambios
       ├─ Clasificador (reglas + Claude API)
       └─ Firestore (estado, histórico, eventos)
Firebase Cloud Messaging → notificaciones push
GitHub Pages → dashboard web

## Stack

- **Backend:** Python 3.12, httpx, pydantic, firebase-admin, anthropic
- **Datos y notificaciones:** Firebase (Firestore + Cloud Messaging)
- **Automatización:** GitHub Actions
- **Frontend:** Vite + Firebase JS SDK, publicado en GitHub Pages

## Estructura

restock-radar/
├─ monitor/            # Lógica del monitor
│  ├─ adapters/        # Un adaptador por tipo de tienda
│  ├─ detector.py      # Detección de cambios
│  ├─ classifier.py    # Clasificación de exclusividad
│  ├─ storage.py       # Acceso a Firestore
│  └─ main.py          # Punto de entrada
├─ config/stores.yaml  # Tiendas monitorizadas
├─ web/                # Dashboard
├─ tests/
└─ .github/workflows/  # Cron del monitor y deploy de Pages

## Configuración local

git clone https://github.com/<tu-usuario>/restock-radar.git
cd restock-radar
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env          # Rellena tus claves

Variables necesarias (en `.env` en local y en **GitHub Secrets** en producción):

| Variable | Descripción |
|---|---|
| `ANTHROPIC_API_KEY` | Clave de la API de Claude |
| `FIREBASE_CREDENTIALS` | JSON de la cuenta de servicio de Firebase |

## Uso responsable

Este proyecto **solo monitorea y notifica**:

- Respeta `robots.txt` y los términos de cada tienda.
- Limita la frecuencia de peticiones y usa caché.
- No evade protecciones anti-bot ni CAPTCHAs.
- No automatiza compras ni checkout.

## Roadmap

- [ ] Adaptador Shopify + detector de cambios
- [ ] Persistencia en Firestore
- [ ] Clasificador de exclusividad
- [ ] Notificaciones push
- [ ] Dashboard web
- [ ] Predicción de restocks
- [ ] Soporte para tiendas no-Shopify

## Licencia

MIT
