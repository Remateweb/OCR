"""
Stream Manager - Captura frames de streams ao vivo (YouTube, RTMP, HLS).
Usa yt-dlp para resolver URLs do YouTube e ffmpeg para captura de frames.
"""

import subprocess
import threading
import time
import os
import io
import re
from PIL import Image


# Detectar se FFmpeg tem suporte a NVDEC (GPU decode)
_ffmpeg_has_gpu = None

def _check_ffmpeg_gpu():
    """Verifica se FFmpeg tem suporte a h264_cuvid (NVIDIA GPU decode)."""
    global _ffmpeg_has_gpu
    if _ffmpeg_has_gpu is not None:
        return _ffmpeg_has_gpu
    try:
        result = subprocess.run(
            ['ffmpeg', '-decoders'],
            capture_output=True, text=True, timeout=5
        )
        _ffmpeg_has_gpu = 'h264_cuvid' in result.stdout
        if _ffmpeg_has_gpu:
            print('[StreamManager] ✅ GPU decode disponível (h264_cuvid)')
        else:
            print('[StreamManager] ℹ️ GPU decode não disponível, usando CPU')
    except Exception:
        _ffmpeg_has_gpu = False
    return _ffmpeg_has_gpu


class StreamManager:
    """Gerencia a captura de frames de um stream ao vivo."""

    def __init__(self, room_id: str, stream_url: str, frames_dir: str = "frames"):
        self.room_id = room_id
        self.stream_url = stream_url
        self.frames_dir = frames_dir
        self.resolved_url = None
        self.stream_type = None  # "youtube", "rtmp", "hls", "direct"
        self.process = None
        self.running = False
        self.current_frame = None  # bytes JPEG
        self.frame_lock = threading.Lock()
        self.capture_thread = None
        self.error = None
        self.use_hw_accel = False  # Se está usando GPU para decode
        self.room_frames_dir = os.path.join(frames_dir, room_id)

        os.makedirs(self.room_frames_dir, exist_ok=True)

    def detect_stream_type(self) -> str:
        """Detecta o tipo de stream pela URL."""
        url = self.stream_url.lower()
        if "youtube.com" in url or "youtu.be" in url:
            return "youtube"
        elif url.startswith("rtmp://"):
            return "rtmp"
        elif ".m3u8" in url:
            return "hls"
        else:
            return "direct"

    def resolve_url(self) -> str:
        """Resolve a URL do stream (especialmente YouTube → m3u8)."""
        self.stream_type = self.detect_stream_type()

        if self.stream_type == "youtube":
            # Estratégias em ordem: sem cookies → arquivo cookies.txt → browser
            import os
            cookies_file = os.path.join(os.path.dirname(__file__), "cookies.txt")

            strategies = [
                ["yt-dlp", "-g", self.stream_url],  # Sem cookies (lives públicas)
            ]
            # Se existe cookies.txt, usar como segunda opção
            if os.path.exists(cookies_file):
                strategies.append(["yt-dlp", "-g", "--cookies", cookies_file, self.stream_url])

            for cmd in strategies:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                    if result.returncode == 0:
                        self.resolved_url = result.stdout.strip().split("\n")[0]
                        self.error = None
                        return self.resolved_url
                    else:
                        stderr = result.stderr
                        # Se precisa de auth, tentar próxima estratégia
                        if "Sign in" in stderr or "bot" in stderr.lower():
                            continue
                        if "not a live" in stderr.lower() or "is not live" in stderr.lower():
                            self.error = "Esta transmissão não está ao vivo. Verifique o link."
                            return None
                        self.error = f"Erro YouTube: {stderr[:150]}"
                except subprocess.TimeoutExpired:
                    self.error = "Timeout ao resolver URL do YouTube"
                except FileNotFoundError:
                    self.error = "yt-dlp não encontrado. Instale com: brew install yt-dlp"
                    return None

            self.error = "Não foi possível acessar o YouTube. Use uma URL direta (RTMP ou HLS/m3u8)."
            return None
        else:
            self.resolved_url = self.stream_url

        return self.resolved_url

    def start(self, interval: float = 1.0):
        """Inicia captura contínua com FFmpeg persistente."""
        if self.running:
            return

        if not self.resolved_url:
            if not self.resolve_url():
                return

        self.running = True
        self._start_ffmpeg_process(interval)

    def _start_ffmpeg_process(self, interval: float = 1.0):
        """Inicia um processo FFmpeg persistente que envia frames via pipe.
        Tenta GPU (NVDEC) primeiro, fallback para CPU."""
        fps = max(0.5, 1.0 / max(interval, 0.5))
        has_gpu = _check_ffmpeg_gpu()

        cmd = [
            "ffmpeg",
            "-fflags", "+nobuffer+discardcorrupt",
            "-flags", "low_delay",
            "-probesize", "16384",
            "-analyzeduration", "100000",
            "-thread_queue_size", "512",
            "-avioflags", "direct",
        ]

        # Reconnect flags só funcionam para HTTP/HLS, não RTMP
        if self.stream_type in ("hls", "youtube", "direct"):
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]

        # GPU decode (NVDEC) — só para streams H.264
        # NVDEC desativado por padrão - pode causar BSOD em drivers instáveis
        # Para ativar: defina USE_NVDEC=1 como variável de ambiente
        use_nvdec = os.environ.get('USE_NVDEC', '1') == '1'
        if use_nvdec and has_gpu and self.stream_type in ("rtmp", "hls", "direct"):
            cmd += [
                "-hwaccel", "cuda",
                "-hwaccel_output_format", "cuda",
                "-c:v", "h264_cuvid",
            ]
            # Com GPU: download do frame GPU→CPU, converter formato, depois fps+scale
            vf = f"hwdownload,format=nv12,fps={fps},scale=640:-1"
            self.use_hw_accel = True
        else:
            vf = f"fps={fps},scale=640:-1"
            self.use_hw_accel = False

        cmd += [
            "-i", self.resolved_url,
            "-vf", vf,
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-q:v", "5",
            "-loglevel", "warning",
            "pipe:1"
        ]

        mode = "GPU (NVDEC)" if self.use_hw_accel else "CPU"
        print(f"[StreamManager] {self.room_id}: Iniciando FFmpeg [{mode}] (fps={fps})")
        print(f"[StreamManager] CMD: {' '.join(cmd[:12])}...")

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1024 * 1024
            )
        except Exception as e:
            self.error = f"Erro ao iniciar FFmpeg: {e}"
            self.running = False
            return

        # Se GPU falhou, tentar CPU no fallback
        # (detectado pelo _read_frames_from_pipe quando process morre rápido)
        self._gpu_failed = False

        # Thread que lê frames do pipe
        self.capture_thread = threading.Thread(
            target=self._read_frames_from_pipe,
            daemon=True
        )
        self.capture_thread.start()

        # Thread que monitora erros
        self._error_thread = threading.Thread(
            target=self._monitor_stderr,
            daemon=True
        )
        self._error_thread.start()

    def _read_frames_from_pipe(self):
        """Lê frames JPEG continuamente do stdout do FFmpeg.
        Detecta limites de JPEG pelos marcadores SOI (0xFFD8) e EOI (0xFFD9)."""
        JPEG_SOI = b'\xff\xd8'
        JPEG_EOI = b'\xff\xd9'
        buffer = b''
        fail_count = 0
        frame_count = 0
        start_time = time.time()

        while self.running and self.process and self.process.poll() is None:
            try:
                chunk = self.process.stdout.read(65536)
                if not chunk:
                    fail_count += 1
                    if fail_count > 30:
                        print(f"[StreamManager] {self.room_id}: Sem dados do FFmpeg, saindo...")
                        break
                    time.sleep(0.05)
                    continue

                fail_count = 0
                buffer += chunk

                # Procurar frames completos no buffer
                while True:
                    soi = buffer.find(JPEG_SOI)
                    if soi == -1:
                        buffer = b''
                        break

                    eoi = buffer.find(JPEG_EOI, soi + 2)
                    if eoi == -1:
                        buffer = buffer[soi:]
                        break

                    frame_bytes = buffer[soi:eoi + 2]
                    buffer = buffer[eoi + 2:]

                    if len(frame_bytes) > 500:
                        frame_count += 1
                        with self.frame_lock:
                            self.current_frame = frame_bytes
                        self._save_frame_to_disk(frame_bytes)
                        self.error = None
                        if frame_count <= 3 or frame_count % 30 == 0:
                            mode = "GPU" if self.use_hw_accel else "CPU"
                            print(f"[StreamManager] {self.room_id}: Frame #{frame_count} [{mode}] ({len(frame_bytes)} bytes)")

            except Exception as e:
                self.error = f"Erro lendo frames: {e}"
                print(f"[StreamManager] {self.room_id}: Erro: {e}")
                break

        # FFmpeg morreu — verificar se GPU falhou rapidamente (fallback para CPU)
        elapsed = time.time() - start_time
        if self.running and self.use_hw_accel and frame_count == 0 and elapsed < 5:
            global _ffmpeg_has_gpu
            print(f"[StreamManager] {self.room_id}: ⚠️ GPU decode falhou ({elapsed:.1f}s, 0 frames). Desativando GPU e tentando CPU...")
            _ffmpeg_has_gpu = False
            self.use_hw_accel = False
            time.sleep(1)
            if self.running:
                self.resolve_url()
                self._start_ffmpeg_process()
            return

        # FFmpeg morreu - reconectar com retry
        if self.running:
            retry_delay = 3
            while self.running:
                print(f"[StreamManager] {self.room_id}: FFmpeg morreu, reconectando em {retry_delay}s...")
                time.sleep(retry_delay)
                if not self.running:
                    break
                self.error = "Reconectando..."
                self.resolve_url()
                self._start_ffmpeg_process()
                break  # O novo _start_ffmpeg_process cria nova thread


    def _monitor_stderr(self):
        """Monitora stderr do FFmpeg para erros."""
        if not self.process:
            return
        try:
            for line in self.process.stderr:
                if not self.running:
                    break
                line = line.decode('utf-8', errors='ignore').strip()
                if line:
                    print(f"[FFmpeg] {self.room_id}: {line[:200]}")
        except Exception:
            pass

    def stop(self):
        """Para a captura e mata o FFmpeg."""
        self.running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        if self.capture_thread:
            self.capture_thread.join(timeout=3)
            self.capture_thread = None

    def _save_frame_to_disk(self, frame_bytes: bytes):
        """Salva o último frame em disco (escrita atômica)."""
        try:
            latest_path = os.path.join(self.room_frames_dir, "latest.jpg")
            tmp_path = latest_path + ".tmp"
            with open(tmp_path, "wb") as f:
                f.write(frame_bytes)
            os.replace(tmp_path, latest_path)  # atômico no mesmo filesystem
        except Exception as e:
            print(f"[StreamManager] Erro ao salvar frame em disco: {e}")

    def _load_frame_from_disk(self) -> bytes:
        """Carrega o último frame salvo do disco (fallback)."""
        try:
            latest_path = os.path.join(self.room_frames_dir, "latest.jpg")
            if os.path.exists(latest_path):
                with open(latest_path, "rb") as f:
                    return f.read()
        except Exception:
            pass
        return None

    def get_current_frame(self) -> bytes:
        """Retorna o frame atual como JPEG bytes. Fallback para disco se memória vazia."""
        with self.frame_lock:
            if self.current_frame:
                return self.current_frame
        # Fallback: carregar do disco (ex: após restart do servidor)
        disk_frame = self._load_frame_from_disk()
        if disk_frame:
            with self.frame_lock:
                self.current_frame = disk_frame
            return disk_frame
        return None

    def get_current_frame_pil(self) -> Image.Image:
        """Retorna o frame atual como PIL Image."""
        frame_bytes = self.get_current_frame()
        if frame_bytes:
            return Image.open(io.BytesIO(frame_bytes))
        return None

    def get_status(self) -> dict:
        """Retorna status do stream."""
        return {
            "running": self.running,
            "stream_type": self.stream_type,
            "has_frame": self.current_frame is not None,
            "error": self.error,
            "hw_accel": self.use_hw_accel,
            "resolved_url": self.resolved_url[:80] + "..." if self.resolved_url and len(self.resolved_url) > 80 else self.resolved_url,
        }
