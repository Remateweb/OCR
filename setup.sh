#!/bin/bash
# ============================================================
# Setup Script - OCR RemateWeb (Servidor Novo)
# ============================================================
# Uso: curl -sSL https://raw.githubusercontent.com/Remateweb/OCR/main/setup.sh | sudo bash
# Ou:  chmod +x setup.sh && sudo ./setup.sh
# ============================================================

set -e

echo "=========================================="
echo "  Setup OCR RemateWeb"
echo "=========================================="

# 1. Instalar Docker
echo "[1/5] Instalando Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    echo "    Docker instalado!"
else
    echo "    Docker ja instalado, pulando."
fi

# 2. Clonar repositorio
echo "[2/5] Clonando repositorio..."
if [ -d "/opt/ocr-remateweb" ]; then
    echo "    Diretorio ja existe, atualizando..."
    cd /opt/ocr-remateweb
    git pull
else
    git clone https://github.com/Remateweb/OCR.git /opt/ocr-remateweb
    cd /opt/ocr-remateweb
fi

# 3. Criar diretorios de dados
echo "[3/5] Criando diretorios..."
mkdir -p data frames

# 4. Build e start
echo "[4/5] Construindo e iniciando container (pode demorar 5-10 min)..."
docker compose -f docker-compose.simple.yml up -d --build

# 5. Aguardar e verificar
echo "[5/5] Verificando..."
sleep 5
if docker ps | grep -q ocr-remateweb; then
    echo ""
    echo "=========================================="
    echo "  Setup concluido!"
    echo ""
    IP=$(curl -s ifconfig.me 2>/dev/null || echo "SEU_IP")
    echo "  Acesse: http://$IP"
    echo "=========================================="
    echo ""
    echo "Comandos uteis:"
    echo "  cd /opt/ocr-remateweb"
    echo "  sudo docker compose -f docker-compose.simple.yml logs -f     # Ver logs"
    echo "  sudo docker compose -f docker-compose.simple.yml restart     # Reiniciar"
    echo "  sudo docker compose -f docker-compose.simple.yml down        # Parar"
    echo "  sudo docker compose -f docker-compose.simple.yml up -d --build  # Rebuild"
else
    echo ""
    echo "  ERRO: Container nao iniciou. Verifique os logs:"
    echo "  docker compose -f docker-compose.simple.yml logs"
fi
