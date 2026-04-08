"""
OCR Extractor (PaddleOCR) - Motor alternativo, mais rapido que EasyOCR.
Mesma interface que ocr_extractor.py para ser intercambiavel.

Usa PaddleOCR (Baidu) para leitura otimizada em CPU.
"""

from paddleocr import PaddleOCR
from PIL import Image, ImageFile

# Permitir frames JPEG truncados (captura ao vivo pode gerar frames incompletos)
ImageFile.LOAD_TRUNCATED_IMAGES = True
import cv2
import numpy as np
import re
import io
import os
import logging

logger = logging.getLogger(__name__)

# ============================================================
# PaddleOCR Reader (singleton)
# ============================================================

_reader = None


def get_reader():
    """Retorna o reader PaddleOCR (singleton para nao recarregar modelo)."""
    global _reader
    if _reader is None:
        logger.info("[OCR-PADDLE] Carregando modelo PaddleOCR (primeira vez)...")
        _reader = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang='pt',
            text_det_thresh=0.3,
            text_detection_model_name='PP-OCRv5_mobile_det',
        )
        logger.info("[OCR-PADDLE] Modelo PaddleOCR carregado!")
    return _reader


# ============================================================
# Preprocessing
# ============================================================

def preprocess_region(img_pil, invert=False):
    """
    Pre-processa uma regiao para OCR.
    Retorna (imagem_para_ocr, imagem_limpa_para_debug) como numpy arrays.
    """
    img_np = np.array(img_pil)

    # Converter para grayscale
    if len(img_np.shape) == 3:
        gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_np

    # Inverter se necessario (texto claro em fundo escuro)
    if invert:
        gray = cv2.bitwise_not(gray)

    # Upscale para crops muito pequenos
    h, w = gray.shape
    if h < 80:
        scale = max(2, 80 // h)
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    clean = gray.copy()
    return gray, clean


# ============================================================
# Extraction
# ============================================================

def _crop_region(img: Image.Image, region: dict, room_id: str = "") -> tuple:
    """Recorta e pre-processa uma regiao (thread-safe: retorna numpy independente).
    Retorna (region_type, ocr_input, room_id) ou None se invalido."""
    w, h = img.size
    region_type = region.get("type", "custom")

    x1 = int(region["x"] * w)
    y1 = int(region["y"] * h)
    x2 = int((region["x"] + region["width"]) * w)
    y2 = int((region["y"] + region["height"]) * h)

    x1 = max(0, min(x1, w))
    y1 = max(0, min(y1, h))
    x2 = max(0, min(x2, w))
    y2 = max(0, min(y2, h))

    if x2 <= x1 or y2 <= y1:
        return None

    cropped = img.crop((x1, y1, x2, y2))
    should_invert = region_type in ("lote", "valor", "nome")
    processed, clean = preprocess_region(cropped, invert=should_invert)

    # Salvar debug crops
    if room_id:
        try:
            debug_dir = os.path.join("frames", room_id, "debug")
            os.makedirs(debug_dir, exist_ok=True)
            cropped.save(os.path.join(debug_dir, f"{region_type}_1_raw.png"))
            cv2.imwrite(
                os.path.join(debug_dir, f"{region_type}_2_processed.png"),
                clean
            )
        except Exception:
            pass

    # Converter para BGR numpy (independente, seguro para threads)
    if len(processed.shape) == 2:
        ocr_input = cv2.cvtColor(processed, cv2.COLOR_GRAY2BGR)
    else:
        ocr_input = processed.copy()

    return (region_type, ocr_input)


def _run_ocr(ocr_input, region_type: str) -> tuple:
    """Roda PaddleOCR num crop ja preparado. Thread-safe (numpy independente).
    Retorna (texto, confianca)."""
    reader = get_reader()
    try:
        results = list(reader.predict(ocr_input))
    except Exception as e:
        logger.error(f"[OCR-PADDLE] Erro: {e}")
        return "", 0

    if not results:
        return "", 0

    result = results[0]
    rec_texts = result.get("rec_texts", [])
    rec_scores = result.get("rec_scores", [])

    if not rec_texts:
        return "", 0

    texts = [str(t).strip() for t in rec_texts if str(t).strip()]
    confs = [float(s) for s in rec_scores[:len(texts)]]

    full_text = " ".join(texts)
    avg_conf = int((sum(confs) / len(confs)) * 100) if confs else 0

    if region_type == "lote":
        logger.info(f"[OCR-PADDLE-LOTE] Textos: {list(zip(texts, [round(c,2) for c in confs]))}")
        logger.info(f"[OCR-PADDLE-LOTE] Texto: '{full_text}' | Conf: {avg_conf}%")

    return full_text, avg_conf


# ============================================================
# Parsers (identicos ao ocr_extractor.py)
# ============================================================

def parse_value(text: str) -> str:
    m = re.search(r"R?\$?\s*([\d]+[.,]?[\d]*)", text)
    return m.group(1) if m else text.strip()


def parse_lote(text: str) -> str:
    text = text.replace('º', '').replace('°', '').replace('ª', '')
    m = re.search(r"LOTE\s*(\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    nums = re.findall(r"(\d+)", text)
    if nums:
        best = max(nums, key=lambda n: (len(n), int(n)))
        logger.info(f"[PARSE-LOTE] Numeros: {nums} -> escolhido: {best}")
        return best
    return text.strip()


def parse_nome(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 3]
    return " | ".join(lines) if lines else text.strip()


# ============================================================
# Main (mesma interface que ocr_extractor.py)
# ============================================================

def extract_all_regions(img: Image.Image, regions: list, room_id: str = "") -> dict:
    """Extrai dados de todas as regioes definidas."""
    results = {}
    raw = {}
    confidence = {}

    parsers = {
        "lote": parse_lote,
        "nome": parse_nome,
        "valor": parse_value,
    }

    for region in regions:
        region_type = region.get("type", "custom")
        crop_data = _crop_region(img, region, room_id=room_id)
        if not crop_data:
            raw[region_type] = ""
            confidence[region_type] = 0
            results[region_type] = "0"
            continue

        _, ocr_input = crop_data
        text, conf = _run_ocr(ocr_input, region_type)
        raw[region_type] = text
        confidence[region_type] = conf

        parser = parsers.get(region_type)
        if conf < 39:
            results[region_type] = "0"
        elif parser:
            results[region_type] = parser(text)
        else:
            results[region_type] = text.strip()

    results["raw"] = raw
    results["confidence"] = confidence
    return results


def extract_from_bytes(frame_bytes: bytes, regions: list, room_id: str = "") -> dict:
    """Extrai dados de um frame em bytes JPEG."""
    img = Image.open(io.BytesIO(frame_bytes))
    return extract_all_regions(img, regions, room_id=room_id)

