"""
OCR Extractor - Extrai dados de regiões selecionadas pelo usuário.
Suporta regiões dinâmicas com coordenadas relativas (0-1).

Usa EasyOCR (deep learning) para leitura robusta de overlays de leilão.
"""

import easyocr
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
# EasyOCR Reader (singleton - carrega modelo uma vez)
# ============================================================

_reader = None


def get_reader():
    """Retorna o reader EasyOCR (singleton para não recarregar modelo)."""
    global _reader
    if _reader is None:
        logger.info("[OCR] Carregando modelo EasyOCR (primeira vez, pode demorar)...")
        _reader = easyocr.Reader(
            ['pt', 'en'],
            gpu=False,
            verbose=False
        )
        logger.info("[OCR] Modelo EasyOCR carregado!")
    return _reader


# ============================================================
# Preprocessing (leve - EasyOCR já lida bem com scene text)
# ============================================================

def preprocess_region(img_pil, invert=False):
    """
    Pré-processa uma região para OCR.
    Com EasyOCR o preprocessing é mínimo: apenas inversão + upscale.
    Retorna (imagem_para_ocr, imagem_limpa_para_debug) como numpy arrays.
    """
    img_np = np.array(img_pil)

    # Converter para grayscale
    if len(img_np.shape) == 3:
        gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_np

    # Inverter se necessário (texto claro em fundo escuro)
    if invert:
        gray = cv2.bitwise_not(gray)

    # Upscale para crops muito pequenos (ex: badge do lote)
    h, w = gray.shape
    if h < 80:
        scale = max(2, 80 // h)
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    # Versão limpa para debug
    clean = gray.copy()

    return gray, clean


# ============================================================
# Extraction
# ============================================================

def extract_text_from_region(img: Image.Image, region: dict, room_id: str = "") -> tuple:
    """Extrai texto e confiança de uma região específica da imagem.
    Retorna (texto, confiança) onde confiança é 0-100."""
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

    # Regiões de leilão sempre têm texto claro em fundo escuro → inverter
    should_invert = region_type in ("lote", "valor", "nome")

    processed, clean = preprocess_region(cropped, invert=should_invert)

    # Salvar debug crops ANTES do OCR (para quando falha)
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

    # Configurar allowlist e parâmetros por tipo
    allowlist = None
    ocr_canvas = 640
    ocr_mag = 1
    if region_type == "valor":
        allowlist = "0123456789.,RS$ "
    elif region_type == "lote":
        allowlist = "0123456789LOTEloteº°ª "
        ocr_canvas = 1280   # canvas maior para detectar dígitos finos como "1"
        ocr_mag = 1.5

    # Rodar EasyOCR
    reader = get_reader()
    try:
        results = reader.readtext(
            processed,
            allowlist=allowlist,
            paragraph=False,
            detail=1,
            decoder='greedy',
            canvas_size=ocr_canvas,
            mag_ratio=ocr_mag
        )
    except Exception as e:
        logger.error(f"[OCR] EasyOCR error: {e}")
        return "", 0

    # Combinar resultados
    if not results:
        return "", 0

    texts = []
    confs = []
    for bbox, text, conf in results:
        text = str(text).strip()
        if text:
            texts.append(text)
            confs.append(float(conf))

    full_text = " ".join(texts)
    avg_conf = int((sum(confs) / len(confs)) * 100) if confs else 0

    # Debug detalhado para lote
    if region_type == "lote":
        logger.info(f"[OCR-LOTE] Resultados brutos: {[(t, round(c, 2)) for _, t, c in results]}")
        logger.info(f"[OCR-LOTE] Texto combinado: '{full_text}' | Conf: {avg_conf}%")

    return full_text, avg_conf


# ============================================================
# Parsers
# ============================================================

def parse_value(text: str) -> str:
    """Extrai valor monetário do texto."""
    m = re.search(r"R?\$?\s*([\d]+[.,]?[\d]*)", text)
    return m.group(1) if m else text.strip()


def parse_lote(text: str) -> str:
    """Extrai número do lote do texto.
    Prioriza o MAIOR número encontrado (o lote real é sempre o número mais longo)."""
    # Remover símbolos ordinais que confundem o OCR (5º → 5)
    text = text.replace('º', '').replace('°', '').replace('ª', '')    # Tentar padrão LOTE + número
    m = re.search(r"LOTE\s*(\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    
    # Fallback: pegar todos os números e retornar o maior (mais dígitos, ou maior valor)
    nums = re.findall(r"(\d+)", text)
    if nums:
        # Priorizar por comprimento (mais dígitos = mais provável ser o lote real)
        # Em caso de empate, pegar o maior valor
        best = max(nums, key=lambda n: (len(n), int(n)))
        logger.info(f"[PARSE-LOTE] Números encontrados: {nums} → escolhido: {best}")
        return best
    return text.strip()


def parse_nome(text: str) -> str:
    """Limpa o nome do animal."""
    lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 3]
    return " | ".join(lines) if lines else text.strip()


# ============================================================
# Main
# ============================================================
from concurrent.futures import ThreadPoolExecutor

_thread_pool = ThreadPoolExecutor(max_workers=4)


def extract_all_regions(img: Image.Image, regions: list, room_id: str = "") -> dict:
    """Extrai dados de todas as regiões definidas (em paralelo)."""
    results = {}
    raw = {}
    confidence = {}

    parsers = {
        "lote": parse_lote,
        "nome": parse_nome,
        "valor": parse_value,
    }

    # Processar todas as regioes em paralelo
    def process_region(region):
        region_type = region.get("type", "custom")
        text, conf = extract_text_from_region(img, region, room_id=room_id)
        return region_type, text, conf

    futures = [_thread_pool.submit(process_region, r) for r in regions]

    for future in futures:
        region_type, text, conf = future.result()
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

