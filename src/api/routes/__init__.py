"""HTTP route modules for the trading dashboard API.

Each module exposes a `make_<domain>_router(...)` factory that takes the
same injectable deps as `api.app.create_app` (store_factory, config_loader,
cache_factory) and returns an APIRouter. Wiring lives in `api.app`.
"""
