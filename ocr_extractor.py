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
_use_gpu = None  # None = auto-detect, True/False = manual override


def _has_gpu():
    """Detecta se há GPU CUDA disponível."""
    try:
        import torch
        available = torch.cuda.is_available()
        if available:
            logger.info(f"[OCR] GPU detectada: {torch.cuda.get_device_name(0)}")
        return available
    except ImportError:
        return False


def get_gpu_info():
    """Retorna info da GPU (nome, memória, utilização) ou None."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        gpu_name = torch.cuda.get_device_name(0)
        mem_allocated = torch.cuda.memory_allocated(0)
        mem_total = torch.cuda.get_device_properties(0).total_mem
        # Tentar pegar utilização via nvidia-smi
        gpu_util = None
        try:
            import subprocess
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(',')
                gpu_util = {
                    'utilization_percent': float(parts[0].strip()),
                    'memory_used_mb': float(parts[1].strip()),
                    'memory_total_mb': float(parts[2].strip()),
                    'temperature_c': float(parts[3].strip()),
                }
        except Exception:
            pass
        return {
            'available': True,
            'name': gpu_name,
            'memory_allocated': mem_allocated,
            'memory_total': mem_total,
            **(gpu_util or {}),
        }
    except ImportError:
        return None
    except Exception:
        return None


def set_gpu_mode(use_gpu: bool):
    """Altera o modo GPU/CPU e reinicializa o reader."""
    global _reader, _use_gpu
    _use_gpu = use_gpu
    if _reader is not None:
        _reader = None  # Forçar recriação no próximo get_reader()
        logger.info(f"[OCR] Modo alterado para {'GPU' if use_gpu else 'CPU'} — reader será recriado")


def is_gpu_enabled():
    """Retorna se GPU está habilitada no momento."""
    global _use_gpu
    if _use_gpu is None:
        return _has_gpu()
    return _use_gpu


def get_reader():
    """Retorna o reader EasyOCR (singleton para não recarregar modelo)."""
    global _reader, _use_gpu
    if _reader is None:
        use_gpu = _use_gpu if _use_gpu is not None else _has_gpu()
        logger.info(f"[OCR] Carregando modelo EasyOCR (GPU={use_gpu})...")
        _reader = easyocr.Reader(
            ['en'],
            gpu=use_gpu,
            verbose=False,
            quantize=not use_gpu,
        )
        logger.info("[OCR] Modelo EasyOCR carregado!")
    return _reader


# ============================================================
# Preprocessing (leve - EasyOCR já lida bem com scene text)
# ============================================================

def preprocess_region(img_pil, invert=False):
    """
    Pré-processa uma região para OCR.
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

    # Redimensionar: mínimo 60px, máximo 150px de altura
    h, w = gray.shape
    if h < 60:
        scale = max(2, 60 // h)
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    elif h > 150:
        scale = 150 / h
        gray = cv2.resize(gray, (int(w * scale), 150), interpolation=cv2.INTER_AREA)

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

    # Configurar allowlist por tipo
    allowlist = None
    if region_type == "valor":
        allowlist = "0123456789.,RS$ "
    elif region_type == "lote":
        allowlist = "0123456789LOTEloteº°ª "

    # Rodar EasyOCR com parâmetros otimizados para velocidade
    reader = get_reader()
    try:
        results = reader.readtext(
            processed,
            allowlist=allowlist,
            paragraph=False,
            detail=1,
            decoder='greedy',
            batch_size=1,
            canvas_size=480,     # Menor canvas = mais rápido
            mag_ratio=1,
            text_threshold=0.5,  # Filtrar texto fraco mais cedo
            low_text=0.3,
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
# Main
# ============================================================

def extract_all_regions(img: Image.Image, regions: list, room_id: str = "") -> dict:
    """Extrai dados de todas as regiões definidas."""
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


