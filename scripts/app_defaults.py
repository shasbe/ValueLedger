"""The default classifier prompts, kept next to the config tool so `init` can
write a usable starter without importing the service."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))
from app.policy import DEFAULT_EXTRACTION_GUIDANCE, DEFAULT_GLOBAL_GUIDANCE  # noqa: E402,F401
