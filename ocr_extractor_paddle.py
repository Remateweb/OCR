"""
OCR Extractor (PaddleOCR) - Motor alternativo, mais rapido que EasyOCR.
Mesma interface que ocr_extractor.py para ser intercambiavel.

Usa PaddleOCR (Baidu) para leitura otimizada em CPU.
"""

from paddleocr import PaddleOCR
from PIL import Image
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
            use_angle_cls=False,
            lang='pt',
            use_gpu=False,
            show_log=False,
            det_db_thresh=0.3,
            rec_batch_num=6,
            enable_mkldnn=True,
            cpu_threads=4,
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

def extract_text_from_region(img: Image.Image, region: dict, room_id: str = "") -> tuple:
    """Extrai texto e confianca de uma regiao especifica da imagem.
    Retorna (texto, confianca) onde confianca e 0-100."""
    w, h = img.size
    region_type = region.get("type", "custom")

    # Converter coordenadas relativas para absolutas
    x1 = int(region["x"] * w)
    y1 = int(region["y"] * h)
    x2 = int((region["x"] + region["width"]) * w)
    y2 = int((region["y"] + region["height"]) * h)

    # Limitar aos bounds
    x1 = max(0, min(x1, w))
    y1 = max(0, min(y1, h))
    x2 = max(0, min(x2, w))
    y2 = max(0, min(y2, h))

    if x2 <= x1 or y2 <= y1:
        return "", 0

    cropped = img.crop((x1, y1, x2, y2))

    # Regioes de leilao sempre tem texto claro em fundo escuro
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

    # Rodar PaddleOCR
    reader = get_reader()
    try:
        # PaddleOCR espera BGR ou grayscale numpy array
        # Converter grayscale para 3 canais para melhor deteccao
        if len(processed.shape) == 2:
            ocr_input = cv2.cvtColor(processed, cv2.COLOR_GRAY2BGR)
        else:
            ocr_input = processed

        results = reader.ocr(ocr_input, cls=False)
    except Exception as e:
        logger.error(f"[OCR-PADDLE] Erro: {e}")
        return "", 0

    # Processar resultados
    if not results or not results[0]:
        return "", 0

    texts = []
    confs = []
    for line in results[0]:
        bbox, (text, conf) = line
        text = str(text).strip()
        if text:
            texts.append(text)
            confs.append(float(conf))

    full_text = " ".join(texts)
    avg_conf = int((sum(confs) / len(confs)) * 100) if confs else 0

    # Debug detalhado para lote
    if region_type == "lote":
        logger.info(f"[OCR-PADDLE-LOTE] Resultados: {[(t, round(c, 2)) for line in results[0] for _, (t, c) in [line]]}")
        logger.info(f"[OCR-PADDLE-LOTE] Texto: '{full_text}' | Conf: {avg_conf}%")

    return full_text, avg_conf


# ============================================================
# Parsers (identicos ao ocr_extractor.py)
# ============================================================

def parse_value(text: str) -> str:
    """Extrai valor monetario do texto."""
    m = re.search(r"R?\$?\s*([\d]+[.,]?[\d]*)", text)
    return m.group(1) if m else text.strip()


def parse_lote(text: str) -> str:
    """Extrai numero do lote do texto."""
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
    """Limpa o nome."""
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
        text, conf = extract_text_from_region(img, region, room_id=room_id)
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
