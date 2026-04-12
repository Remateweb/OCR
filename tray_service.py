"""
OCR RemateWeb - System Tray Service (Windows)
Roda o servidor OCR em background com ícone na bandeja do sistema.
"""

import subprocess
import sys
import os
import threading
import time
import webbrowser
import signal

# Porta do servidor
PORT = 8080
URL = f"http://localhost:{PORT}"

server_process = None
server_running = False


def create_icon_image():
    """Cria ícone programaticamente (sem arquivo externo)."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Fundo arredondado verde
    draw.rounded_rectangle([2, 2, 62, 62], radius=12, fill=(0, 110, 150, 255))
    # Texto "OCR"
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except:
        font = ImageFont.load_default()
    draw.text((10, 20), "OCR", fill=(255, 255, 255, 255), font=font)
    return img


def start_server():
    """Inicia o servidor uvicorn em background."""
    global server_process, server_running
    if server_process and server_process.poll() is None:
        return  # Já rodando

    env = os.environ.copy()
    server_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", str(PORT)],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
    )
    server_running = True
    print(f"[OCR] Servidor iniciado (PID: {server_process.pid}) em {URL}")


def stop_server():
    """Para o servidor."""
    global server_process, server_running
    server_running = False
    if server_process:
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
        server_process = None
        print("[OCR] Servidor parado.")


def open_dashboard(icon=None, item=None):
    """Abre o dashboard no navegador."""
    webbrowser.open(URL)


def restart_server(icon=None, item=None):
    """Reinicia o servidor."""
    stop_server()
    time.sleep(1)
    start_server()
    if icon:
        icon.notify("Servidor reiniciado!", "OCR RemateWeb")


def quit_app(icon, item):
    """Encerra tudo."""
    stop_server()
    icon.stop()


def update_title(icon):
    """Atualiza o tooltip do ícone com status."""
    while server_running:
        if server_process and server_process.poll() is None:
            icon.title = f"OCR RemateWeb - Rodando (porta {PORT})"
        else:
            icon.title = "OCR RemateWeb - Parado"
        time.sleep(5)


def main():
    import pystray
    from pystray import MenuItem

    # Criar ícone
    image = create_icon_image()

    menu = pystray.Menu(
        MenuItem("🌐 Abrir Dashboard", open_dashboard, default=True),
        MenuItem("🔄 Reiniciar Servidor", restart_server),
        pystray.Menu.SEPARATOR,
        MenuItem("❌ Encerrar", quit_app),
    )

    icon = pystray.Icon(
        name="OCR RemateWeb",
        icon=image,
        title=f"OCR RemateWeb - Iniciando...",
        menu=menu,
    )

    # Iniciar servidor em thread separada
    def setup(icon):
        icon.visible = True
        start_server()
        # Abrir dashboard automaticamente
        time.sleep(2)
        open_dashboard()
        # Atualizar tooltip
        update_title(icon)

    icon.run(setup)


if __name__ == "__main__":
    main()
