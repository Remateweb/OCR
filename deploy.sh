#!/bin/bash
# ============================================================
# Deploy Script - OCR RemateWeb
# ============================================================
# Uso: ./deploy.sh SEU_DOMINIO.com SEU_EMAIL@email.com
# ============================================================

set -e

DOMAIN=$1
EMAIL=$2

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "❌ Uso: ./deploy.sh SEU_DOMINIO.com SEU_EMAIL@email.com"
    exit 1
fi

echo "=========================================="
echo "  Deploy OCR RemateWeb"
echo "  Domínio: $DOMAIN"
echo "  Email:   $EMAIL"
echo "=========================================="

# 1. Substituir domínio nos arquivos de config
echo "[1/6] Configurando domínio..."
sed -i "s/SEU_DOMINIO.com/$DOMAIN/g" nginx/nginx.conf
sed -i "s/SEU_DOMINIO.com/$DOMAIN/g" nginx/nginx-init.conf

# 2. Criar diretórios necessários
echo "[2/6] Criando diretórios..."
mkdir -p data frames certbot/conf certbot/www

# 3. Usar config HTTP temporário para gerar SSL
echo "[3/6] Subindo containers (HTTP)..."
cp nginx/nginx.conf nginx/nginx-ssl.conf.bkp
cp nginx/nginx-init.conf nginx/nginx.conf

docker compose up -d --build

# Aguardar nginx subir
echo "    Aguardando nginx..."
sleep 10

# 4. Gerar certificado SSL
echo "[4/6] Gerando certificado SSL com Let's Encrypt..."
docker compose run --rm certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email $EMAIL \
    --agree-tos \
    --no-eff-email \
    -d $DOMAIN

# 5. Restaurar config HTTPS
echo "[5/6] Ativando HTTPS..."
cp nginx/nginx-ssl.conf.bkp nginx/nginx.conf
rm nginx/nginx-ssl.conf.bkp

# 6. Reiniciar nginx com SSL
echo "[6/6] Reiniciando nginx com SSL..."
docker compose restart nginx

echo ""
echo "=========================================="
echo "  ✅ Deploy concluído!"
echo "  Acesse: https://$DOMAIN"
echo "=========================================="
echo ""
echo "Comandos úteis:"
echo "  docker compose logs -f        # Ver logs"
echo "  docker compose restart        # Reiniciar"
echo "  docker compose down           # Parar tudo"
echo "  docker compose up -d --build  # Rebuild"
echo ""
echo "Para renovar SSL manualmente:"
echo "  docker compose run --rm certbot renew"
echo "  docker compose restart nginx"
