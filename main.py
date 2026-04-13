"""
repi-parser: Instruction parsing service for Repi recipe app.
Runs on localhost only — not exposed to public internet.

Stack: FastAPI + llama-cpp-python (Qwen2.5-0.5B-Instruct Q4_K_M)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
import logging

from parser_engine import InstructionParser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

parser: InstructionParser = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global parser
    logger.info("Loading instruction parser...")
    parser = InstructionParser()
    logger.info("Parser ready.")
    yield
    logger.info("Shutting down parser.")


app = FastAPI(
    title="repi-parser",
    description="Cooking instruction structured data extractor",
    version="1.0.0",
    lifespan=lifespan,
)

# Only allow requests from localhost (Repi API on same VPS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)


class ParseRequest(BaseModel):
    text: str


class ParseResponse(BaseModel):
    appliance: str | None = None
    temp_min_celsius: float | None = None
    temp_max_celsius: float | None = None
    flame_level: str | None = None          # "low" | "medium" | "high"
    duration_min_minutes: float | None = None
    duration_max_minutes: float | None = None
    timer_type: str | None = None           # "passive" | "active" | "resting"
    confidence: float = 0.0
    parsed_by: str = "none"                 # "regex" | "model" | "none"


@app.post("/parse", response_model=ParseResponse)
async def parse_instruction(req: ParseRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="text too long (max 1000 chars)")

    result = parser.parse(text)
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "model_loaded": parser is not None and parser.model_loaded}
