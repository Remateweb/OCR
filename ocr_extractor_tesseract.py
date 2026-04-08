"""
OCR Extractor (Tesseract) - Motor leve e rapido.
Mesma interface que ocr_extractor.py para ser intercambiavel.

Usa Tesseract OCR para leitura ultrarrápida em CPU.
Ideal para overlays digitais com texto grande.
"""

import pytesseract
from PIL import Image, ImageFile

# Permitir frames JPEG truncados
ImageFile.LOAD_TRUNCATED_IMAGES = True

import cv2
import numpy as np
import re
import io
import os
import logging

logger = logging.getLogger(__name__)


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

    # Upscale para crops muito pequenos (Tesseract precisa de texto >= 30px)
    h, w = gray.shape
    if h < 80:
        scale = max(2, 80 // h)
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    # Threshold para binarizar (Tesseract funciona melhor com imagem binaria)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    clean = gray.copy()
    return binary, clean


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

    # Configurar Tesseract por tipo de regiao
    if region_type == "valor":
        # Apenas digitos e pontuacao monetaria
        config = "--psm 7 -c tessedit_char_whitelist=0123456789.,"
    elif region_type == "lote":
        # Linha unica com letras e numeros
        config = "--psm 7"
    else:
        # Texto generico (condicao de pagamento, etc)
        config = "--psm 7"

    # Rodar Tesseract
    try:
        # Usar image_to_data para obter confianca
        pil_processed = Image.fromarray(processed)
        data = pytesseract.image_to_data(pil_processed, lang='por', config=config, output_type=pytesseract.Output.DICT)

        texts = []
        confs = []
        for i, text in enumerate(data['text']):
            text = str(text).strip()
            conf = int(data['conf'][i])
            if text and conf > 0:
                texts.append(text)
                confs.append(conf)

        full_text = " ".join(texts)
        avg_conf = int(sum(confs) / len(confs)) if confs else 0

    except Exception as e:
        logger.error(f"[OCR-TESS] Erro: {e}")
        return "", 0

    # Debug detalhado para lote
    if region_type == "lote":
        logger.info(f"[OCR-TESS-LOTE] Textos: {list(zip(texts, confs))}")
        logger.info(f"[OCR-TESS-LOTE] Texto: '{full_text}' | Conf: {avg_conf}%")

    return full_text, avg_conf


# ============================================================
# Parsers
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


def parse_condicao(text: str) -> str:
    """Normaliza condicao de pagamento: extrai numeros e formata X + Y = Z."""
    nums = re.findall(r"(\d+)", text)
    if len(nums) >= 3:
        return f"{nums[0]} + {nums[1]} = {nums[2]}"
    elif len(nums) == 2:
        return f"{nums[0]} + {nums[1]}"
    elif len(nums) == 1:
        return nums[0]
    return text.strip()


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
        "nome": parse_condicao,
        "valor": parse_value,
    }

    for region in regions:
        region_type = region.get("type", "custom")
        text, conf = extract_text_from_region(img, region, room_id=room_id)
        raw[region_type] = text
        confidence[region_type] = conf

        parser = parsers.get(region_type)
        if conf < 30:
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
