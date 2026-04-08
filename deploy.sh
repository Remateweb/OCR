#!/bin/bash
# ============================================================
# Deploy Script - OCR RemateWeb (AWS EC2 / VPS)
# ============================================================
# Uso na instância EC2:
#   1. SSH na instância
#   2. curl -sSL https://raw.githubusercontent.com/Remateweb/OCR/main/deploy.sh | sudo bash
#   Ou: chmod +x deploy.sh && sudo ./deploy.sh
# ============================================================

set -e

echo ""
echo "============================================"
echo "   OCR RemateWeb - Deploy Script"
echo "============================================"
echo ""

# -----------------------------------------------
# 1. Atualizar sistema e instalar dependências
# -----------------------------------------------
echo "[1/6] Atualizando sistema..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    git \
    python3 \
    python3-pip \
    python3-venv \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    htop

echo "    Sistema atualizado!"

# -----------------------------------------------
# 2. Clonar ou atualizar repositório
# -----------------------------------------------
echo "[2/6] Configurando repositório..."
APP_DIR="/opt/ocr-remateweb"

if [ -d "$APP_DIR" ]; then
    echo "    Diretório já existe, atualizando..."
    cd "$APP_DIR"
    git pull origin main 2>/dev/null || git pull
else
    git clone https://github.com/Remateweb/OCR.git "$APP_DIR"
    cd "$APP_DIR"
fi

echo "    Repositório configurado!"

# -----------------------------------------------
# 3. Criar ambiente virtual Python
# -----------------------------------------------
echo "[3/6] Configurando ambiente Python..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "    Ambiente Python configurado!"

# -----------------------------------------------
# 4. Criar diretórios necessários
# -----------------------------------------------
echo "[4/6] Criando diretórios..."
mkdir -p data frames output

echo "    Diretórios criados!"

# -----------------------------------------------
# 5. Configurar serviço systemd
# -----------------------------------------------
echo "[5/6] Configurando serviço systemd..."

cat > /etc/systemd/system/ocr-remateweb.service << 'SYSTEMD'
[Unit]
Description=OCR RemateWeb Service
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/ocr-remateweb
Environment=PATH=/opt/ocr-remateweb/venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=DB_PATH=/opt/ocr-remateweb/data/ocr_rooms.db
ExecStart=/opt/ocr-remateweb/venv/bin/uvicorn server:app --host 0.0.0.0 --port 80
Restart=always
RestartSec=5

# Logs
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ocr-remateweb

# Limites
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable ocr-remateweb
systemctl restart ocr-remateweb

echo "    Serviço systemd configurado e iniciado!"

# -----------------------------------------------
# 6. Verificar
# -----------------------------------------------
echo "[6/6] Verificando..."
sleep 3

if systemctl is-active --quiet ocr-remateweb; then
    IP=$(curl -s ifconfig.me 2>/dev/null || echo "SEU_IP")
    echo ""
    echo "============================================"
    echo "   Deploy concluído com sucesso!"
    echo ""
    echo "   Acesse: http://$IP"
    echo "============================================"
    echo ""
    echo "Comandos úteis:"
    echo "  sudo systemctl status ocr-remateweb    # Status"
    echo "  sudo journalctl -u ocr-remateweb -f    # Ver logs"
    echo "  sudo systemctl restart ocr-remateweb   # Reiniciar"
    echo "  sudo systemctl stop ocr-remateweb      # Parar"
    echo ""
    echo "Para atualizar código:"
    echo "  cd /opt/ocr-remateweb && git pull && sudo systemctl restart ocr-remateweb"
    echo ""
else
    echo ""
    echo "  ERRO: Serviço não iniciou!"
    echo "  Verifique: sudo journalctl -u ocr-remateweb -n 50"
    echo ""
fi
