"""Application entrypoint for uvicorn."""

from app.main_factory import create_app

app = create_app()
