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

    def capture_single_frame(self) -> bytes:
        """Captura um único frame do stream usando ffmpeg."""
        if not self.resolved_url:
            if not self.resolve_url():
                return None

        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-i", self.resolved_url,
                "-frames:v", "1",
                "-f", "image2pipe",
                "-vcodec", "mjpeg",
                "-q:v", "2",
                "-loglevel", "error",
                "pipe:1"
            ]

            result = subprocess.run(
                cmd, capture_output=True, timeout=5
            )

            if result.returncode == 0 and len(result.stdout) > 0:
                frame_bytes = result.stdout
                with self.frame_lock:
                    self.current_frame = frame_bytes
                # Salvar em disco
                self._save_frame_to_disk(frame_bytes)
                return frame_bytes
            else:
                self.error = f"ffmpeg error: {result.stderr.decode()[:200]}"
                return None

        except subprocess.TimeoutExpired:
            self.error = "ffmpeg timeout capturando frame"
            return None
        except Exception as e:
            self.error = str(e)
            return None

    def _capture_loop(self, interval: float):
        """Loop de captura contínua de frames."""
        fail_count = 0
        while self.running:
            frame = self.capture_single_frame()
            if frame:
                self.error = None
                fail_count = 0
            else:
                fail_count += 1
                # Após 3 falhas consecutivas, re-resolver URL
                if fail_count % 3 == 0:
                    print(f"[StreamManager] {self.room_id}: {fail_count} falhas, re-resolvendo URL...")
                    self.resolve_url()
            time.sleep(interval)

    def start(self, interval: float = 1.0):
        """Inicia captura contínua de frames."""
        if self.running:
            return

        if not self.resolved_url:
            if not self.resolve_url():
                return

        self.running = True
        self.capture_thread = threading.Thread(
            target=self._capture_loop,
            args=(interval,),
            daemon=True
        )
        self.capture_thread.start()

    def stop(self):
        """Para a captura."""
        self.running = False
        if self.capture_thread:
            self.capture_thread.join(timeout=5)
            self.capture_thread = None

    def _save_frame_to_disk(self, frame_bytes: bytes):
        """Salva o último frame em disco."""
        try:
            latest_path = os.path.join(self.room_frames_dir, "latest.jpg")
            with open(latest_path, "wb") as f:
                f.write(frame_bytes)
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
            "resolved_url": self.resolved_url[:80] + "..." if self.resolved_url and len(self.resolved_url) > 80 else self.resolved_url,
        }
