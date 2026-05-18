"""
parser_engine.py

Two-layer parsing:
  Layer 1 — Regex: instant, handles clear numeric patterns
  Layer 2 — Qwen2.5-0.5B: handles natural language, ranges, Vietnamese text

Confidence threshold: if regex confidence >= 0.85, skip model call.
"""

import re
import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ParseResult:
    appliance: Optional[str] = None
    temp_min_celsius: Optional[float] = None
    temp_max_celsius: Optional[float] = None
    flame_level: Optional[str] = None
    duration_min_minutes: Optional[float] = None
    duration_max_minutes: Optional[float] = None
    timer_type: Optional[str] = None
    confidence: float = 0.0
    parsed_by: str = "none"

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# Regex Layer
# ---------------------------------------------------------------------------

# Vietnamese number words → float
VN_NUMBERS = {
    "một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5,
    "sáu": 6, "bảy": 7, "tám": 8, "chín": 9, "mười": 10,
    "mươi": 10, "mười lăm": 15, "hai mươi": 20, "hai mươi lăm": 25,
    "ba mươi": 30, "bốn mươi": 40, "năm mươi": 50,
    "một tiếng": 60, "hai tiếng": 120, "ba tiếng": 180,
    "nửa tiếng": 30, "nửa giờ": 30,
    "một giờ": 60, "hai giờ": 120, "ba giờ": 180,
}

APPLIANCE_KEYWORDS = {
    # Order matters — more specific entries first to avoid early false matches
    "airfryer":    ["nồi chiên không dầu", "air fryer", "airfryer", "chiên không dầu"],
    "microwave":   ["lò vi sóng", "vi sóng", "microwave"],
    "instant_pot": ["instant pot", "nồi áp suất", "pressure cooker", "áp suất"],
    "rice_cooker": ["nồi cơm điện", "rice cooker"],
    "steamer":     ["hấp", "steamer", "nồi hấp"],  # "steam" removed — too broad
    "grill":       ["vỉ nướng", "grille", "grill", "bbq", "nướng vỉ"],
    "mixer":       ["máy trộn", "kitchen aid", "kitchenaid", "mixer", "máy đánh trứng", "đánh bông", "trộn bột"],
    "blender":     ["máy xay", "blender", "xay nhuyễn", "xay mịn", "blend"],
    # oven: "nướng" alone maps to oven (most common context), but after more specific matches
    "oven":        ["lò nướng", "lò", "oven", "nướng lò", "bake", "roast", "nướng"],
    # stovetop: "hầm" (braise) maps here — common stovetop technique
    "stovetop":    ["bếp", "chảo", "stovetop", "stove", "pan", "wok", "xào", "chiên", "luộc", "kho", "hầm"],
}

FLAME_KEYWORDS = {
    "high":   ["lửa to", "lửa lớn", "high heat", "lửa mạnh", "nhiệt cao", "high"],
    "medium": ["lửa vừa", "lửa trung bình", "medium heat", "medium", "vừa lửa"],
    "low":    ["lửa nhỏ", "nhỏ lửa", "lửa liu riu", "liu riu", "low heat", "lửa thấp", "nhiệt thấp", "low", "simmer"],
}

TIMER_PASSIVE_KEYWORDS = ["nướng", "hầm", "ủ", "để yên", "nghỉ", "ngâm", "bake", "roast", "simmer", "marinate", "rest", "steep"]
TIMER_RESTING_KEYWORDS = ["nghỉ", "ủ bột", "để bột", "để nguội", "rest", "cool", "ủ"]


def _parse_vn_number(text: str) -> Optional[float]:
    """Try to convert Vietnamese number words in text to float."""
    text_lower = text.lower().strip()
    # Sort by length desc to match longest first
    for word, val in sorted(VN_NUMBERS.items(), key=lambda x: -len(x[0])):
        if word in text_lower:
            return float(val)
    return None


def _extract_temperature(text: str) -> tuple[Optional[float], Optional[float], str]:
    """
    Returns (temp_min, temp_max, unit) where unit is 'C' or 'F'.
    Handles: "180°C", "180 độ", "175-180 độ C", "350F", "350 degrees F"
    """
    text_lower = text.lower()

    # Detect unit
    unit = "C"
    if re.search(r"°f|fahrenheit|\bf\b(?!\w)", text_lower):
        unit = "F"

    # Range pattern: "175-180" or "175 đến 180" or "175 to 180"
    range_pat = re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:[-–]|đến|to)\s*(\d+(?:\.\d+)?)"
        r"\s*(?:°?c|°?f|độ c|độ f|độ|degrees?)?",
        re.IGNORECASE,
    )
    m = range_pat.search(text)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if unit == "F":
            lo, hi = (lo - 32) * 5/9, (hi - 32) * 5/9
        return round(lo, 1), round(hi, 1), unit

    # Single value: "180°C", "180 độ", "350F"
    single_pat = re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:°c|°f|độ c|độ f|độ|°|degrees?)",
        re.IGNORECASE,
    )
    m = single_pat.search(text)
    if m:
        val = float(m.group(1))
        if unit == "F":
            val = (val - 32) * 5/9
        return round(val, 1), None, unit

    return None, None, unit


def _extract_duration(text: str) -> tuple[Optional[float], Optional[float]]:
    """
    Returns (min_minutes, max_minutes).
    Handles: "25 phút", "25-30 phút", "1 tiếng 30 phút", "nửa tiếng", "2 hours"
    """
    text_lower = text.lower()

    result_minutes = 0.0
    found = False

    # Hours (giờ/tiếng/hour)
    hour_range = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:giờ|tiếng|hours?|hrs?)\s*(?:(\d+)\s*(?:phút|minutes?|min))?",
        text_lower,
    )
    if hour_range:
        result_minutes += float(hour_range.group(1)) * 60
        if hour_range.group(2):
            result_minutes += float(hour_range.group(2))
        found = True

    # "nửa tiếng" / "nửa giờ"
    if re.search(r"nửa\s*(?:tiếng|giờ|hour)", text_lower):
        result_minutes += 30
        found = True

    # Minutes range: "25-30 phút"
    min_range = re.search(
        r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(?:phút|minutes?|min)",
        text_lower,
    )
    if min_range and not found:
        lo, hi = float(min_range.group(1)), float(min_range.group(2))
        return lo, hi

    # Minutes single: "25 phút"
    min_single = re.search(r"(\d+(?:\.\d+)?)\s*(?:phút|minutes?|min)", text_lower)
    if min_single and not found:
        result_minutes += float(min_single.group(1))
        found = True
    elif min_single and found:
        # Already counted from hours pattern above
        pass

    # Vietnamese number words for time
    if not found:
        for phrase, val in sorted(VN_NUMBERS.items(), key=lambda x: -len(x[0])):
            if phrase in text_lower and ("phút" in text_lower or "tiếng" in text_lower or "giờ" in text_lower):
                multiplier = 60 if any(w in text_lower for w in ["tiếng", "giờ", "hour"]) else 1
                result_minutes = val * multiplier
                found = True
                break

    if found and result_minutes > 0:
        return result_minutes, None

    return None, None


def _detect_appliance(text: str) -> Optional[str]:
    text_lower = text.lower()
    for appliance, keywords in APPLIANCE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return appliance
    return None


def _detect_flame(text: str) -> Optional[str]:
    text_lower = text.lower()
    for level, keywords in FLAME_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return level
    return None


def _detect_timer_type(text: str, appliance: Optional[str]) -> Optional[str]:
    text_lower = text.lower()
    for kw in TIMER_RESTING_KEYWORDS:
        if kw in text_lower:
            return "resting"
    for kw in TIMER_PASSIVE_KEYWORDS:
        if kw in text_lower:
            return "passive"
    if appliance in ("oven", "instant_pot", "steamer", "rice_cooker"):
        return "passive"
    if appliance == "stovetop":
        return "active"
    return None


def regex_parse(text: str) -> ParseResult:
    result = ParseResult(parsed_by="regex")

    temp_min, temp_max, _ = _extract_temperature(text)
    result.temp_min_celsius = temp_min
    result.temp_max_celsius = temp_max

    dur_min, dur_max = _extract_duration(text)
    result.duration_min_minutes = dur_min
    result.duration_max_minutes = dur_max

    result.appliance = _detect_appliance(text)
    result.flame_level = _detect_flame(text)
    result.timer_type = _detect_timer_type(text, result.appliance)

    # Confidence scoring
    score = 0.0
    signals = 0
    if temp_min is not None:
        score += 0.4
        signals += 1
    if dur_min is not None:
        score += 0.35
        signals += 1
    if result.appliance:
        score += 0.15
        signals += 1
    if result.flame_level:
        score += 0.1
        signals += 1

    # No structured data found at all
    if signals == 0:
        result.confidence = 0.0
        result.parsed_by = "none"
    else:
        result.confidence = min(score, 1.0)

    return result


# ---------------------------------------------------------------------------
# Model Layer (Qwen2.5-0.5B via llama-cpp-python)
# ---------------------------------------------------------------------------

MODEL_PATH = os.environ.get("MODEL_PATH", "/app/models/qwen2.5-0.5b-instruct-q4_k_m.gguf")

FEW_SHOT_EXAMPLES = """Parse Vietnamese/English cooking instructions. Return ONLY valid JSON with these optional fields:
appliance (oven|stovetop|airfryer|mixer|blender|instant_pot|steamer|microwave|rice_cooker|grill),
temp_min_celsius (number), temp_max_celsius (number), flame_level (low|medium|high),
duration_min_minutes (number), duration_max_minutes (number),
timer_type (passive|active|resting).

Examples:
"Nướng ở 180 độ trong 25 phút" -> {"appliance":"oven","temp_min_celsius":180,"duration_min_minutes":25,"timer_type":"passive"}
"Xào với lửa vừa tầm 5-7 phút" -> {"appliance":"stovetop","flame_level":"medium","duration_min_minutes":5,"duration_max_minutes":7,"timer_type":"active"}
"Blend mịn" -> {"appliance":"blender"}
"Để bột nghỉ 30 phút" -> {"timer_type":"resting","duration_min_minutes":30}
"Hầm nhỏ lửa 1 tiếng 30 phút" -> {"appliance":"stovetop","flame_level":"low","duration_min_minutes":90,"timer_type":"passive"}
"Cho vào lò ở khoảng 175-180 độ, nướng tầm nửa tiếng" -> {"appliance":"oven","temp_min_celsius":175,"temp_max_celsius":180,"duration_min_minutes":30,"timer_type":"passive"}
"Đánh bông bơ với đường bằng máy trộn tốc độ cao 5 phút" -> {"appliance":"mixer","duration_min_minutes":5,"timer_type":"active"}
"Pressure cook 15 minutes" -> {"appliance":"instant_pot","duration_min_minutes":15,"timer_type":"passive"}
"Bake at 350°F for 30-35 minutes" -> {"appliance":"oven","temp_min_celsius":176.7,"duration_min_minutes":30,"duration_max_minutes":35,"timer_type":"passive"}
"""


class InstructionParser:
    def __init__(self):
        self.model_loaded = False
        self._llm = None
        self._try_load_model()

    def _try_load_model(self):
        try:
            from llama_cpp import Llama
            import os
            if not os.path.exists(MODEL_PATH):
                logger.warning(f"Model not found at {MODEL_PATH}. Regex-only mode active.")
                return
            logger.info(f"Loading model from {MODEL_PATH}...")
            self._llm = Llama(
                model_path=MODEL_PATH,
                n_ctx=512,        # small context = faster + less RAM
                n_threads=2,      # match VPS vCPU count
                n_gpu_layers=0,   # CPU only
                verbose=False,
            )
            self.model_loaded = True
            logger.info("Model loaded successfully.")
        except ImportError:
            logger.warning("llama-cpp-python not installed. Regex-only mode.")
        except Exception as e:
            logger.error(f"Failed to load model: {e}. Regex-only mode.")

    def _model_parse(self, text: str) -> Optional[ParseResult]:
        if not self._llm:
            return None
        try:
            prompt = f"{FEW_SHOT_EXAMPLES}\n\"{text}\" ->"
            output = self._llm(
                prompt,
                max_tokens=128,
                temperature=0.0,    # deterministic
                stop=["\n", "\"\"\""],
            )
            raw = output["choices"][0]["text"].strip()

            # Extract JSON safely
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start == -1 or end == 0:
                return None

            data = json.loads(raw[start:end])
            result = ParseResult(
                appliance=data.get("appliance"),
                temp_min_celsius=data.get("temp_min_celsius"),
                temp_max_celsius=data.get("temp_max_celsius"),
                flame_level=data.get("flame_level"),
                duration_min_minutes=data.get("duration_min_minutes"),
                duration_max_minutes=data.get("duration_max_minutes"),
                timer_type=data.get("timer_type"),
                confidence=0.88,
                parsed_by="model",
            )
            return result
        except Exception as e:
            logger.warning(f"Model parse failed: {e}")
            return None

    def parse(self, text: str) -> ParseResult:
        # Layer 1: regex
        regex_result = regex_parse(text)

        # If regex is confident enough, skip model
        if regex_result.confidence >= 0.85:
            return regex_result

        # Layer 2: model
        model_result = self._model_parse(text)
        if model_result:
            return model_result

        # Fallback: return whatever regex found (even low confidence)
        return regex_result
