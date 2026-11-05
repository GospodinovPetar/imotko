"""Настройките на проекта на едно място: .env, папка за данните и моделите."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# IMOT_DATA_DIR позволява базите да са извън кода (напр. volume в Docker)
DATA_DIR = Path(os.getenv("IMOT_DATA_DIR", PROJECT_ROOT))

MAIN_MODEL = os.getenv("IMOT_MODEL", "google/gemma-4-31b-it:free")
# безплатните модели често връщат 429, затова има резервен
FALLBACK_MODEL = os.getenv("IMOT_FALLBACK_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")

# Само мултимодални модели. Gemma 4 приема снимки, Nemotron Ultra - не.
VISION_MODEL = os.getenv("IMOT_VISION_MODEL", "google/gemma-4-31b-it:free")
VISION_FALLBACK_MODEL = os.getenv("IMOT_VISION_FALLBACK_MODEL", "nvidia/nemotron-3.5-super-vl:free")
