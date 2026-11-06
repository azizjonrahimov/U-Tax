from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import __version__
from app.errors import install_error_handlers

FRONTEND_ORIGIN = "http://localhost:3000"

app = FastAPI(title="Naive YC Hackathon API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handlers(app)


class Health(BaseModel):
    status: str
    version: str


@app.get("/api/health", response_model=Health)
def health() -> Health:
    return Health(status="ok", version=__version__)
