<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/EasyOCR-FF6F00?style=for-the-badge&logo=tensorflow&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
  <img src="https://img.shields.io/badge/FFmpeg-007808?style=for-the-badge&logo=ffmpeg&logoColor=white" />
</p>

---

# OCR RemateWeb

Sistema de extracao de dados de leiloes ao vivo via OCR (Optical Character Recognition) em tempo real. Captura frames de transmissoes ao vivo (YouTube Live, RTMP, HLS), aplica reconhecimento optico de caracteres nas regioes configuradas e envia os dados extraidos para a API RemateWeb automaticamente.

---

## Indice

- [Visao Geral](#visao-geral)
- [Arquitetura](#arquitetura)
- [Requisitos](#requisitos)
- [Instalacao Local](#instalacao-local)
- [Deploy em Producao](#deploy-em-producao)
- [Estrutura de Arquivos](#estrutura-de-arquivos)
- [Rotas da API](#rotas-da-api)
- [WebSocket](#websocket)
- [Modulos](#modulos)
- [Banco de Dados](#banco-de-dados)
- [Monitoramento do Sistema](#monitoramento-do-sistema)
- [Integracoes Externas](#integracoes-externas)
- [Configuracao](#configuracao)
- [Troubleshooting](#troubleshooting)

---

## Visao Geral

O sistema funciona como um pipeline de 4 etapas:

```
Transmissao ao Vivo  -->  Captura de Frames  -->  OCR nas Regioes  -->  API RemateWeb
   (YouTube/RTMP)         (FFmpeg + yt-dlp)       (EasyOCR)             (POST /api/ocr/bid)
```

**Funcionalidades principais:**

- Gerenciamento de multiplas salas simultaneas, cada uma com seu stream
- Selecao visual de regioes de extracao (lote, valor, condicao de pagamento)
- OCR com EasyOCR (deep learning, suporte a portugues e ingles)
- Deteccao automatica de troca de lote com historico de lances
- Envio automatico de dados para a API RemateWeb
- Alertas via Telegram quando o stream cai
- Templates de regioes reutilizaveis entre salas
- Monitoramento de recursos do servidor (CPU, RAM, disco) em tempo real
- Interface web responsiva com tema escuro

---

## Arquitetura

```
                                 +------------------+
                                 |   Browser (UI)   |
                                 |  index.html      |
                                 |  room.html       |
                                 |  login.html      |
                                 +--------+---------+
                                          |
                                   HTTP / WebSocket
                                          |
                                 +--------+---------+
                                 |     FastAPI       |
                                 |    server.py      |
                                 +---+-----+----+---+
                                     |     |    |
                        +------------+     |    +-------------+
                        |                  |                  |
               +--------+-------+  +------+------+  +--------+--------+
               | StreamManager  |  | OCR Extractor|  |   SQLite (DB)   |
               | stream_manager |  | ocr_extractor|  |  ocr_rooms.db   |
               +--------+-------+  +------+------+  +-----------------+
                        |                  |
                   +----+----+        +----+----+
                   |  yt-dlp |        | EasyOCR |
                   |  FFmpeg |        | OpenCV  |
                   +---------+        +---------+
```

---

## Requisitos

### Desenvolvimento Local

| Dependencia | Versao Minima | Finalidade                           |
|-------------|---------------|--------------------------------------|
| Python      | 3.11+         | Runtime principal                    |
| FFmpeg      | 6.0+          | Captura de frames de streams         |
| yt-dlp      | latest        | Resolucao de URLs do YouTube         |

### Producao (Docker)

| Dependencia       | Versao Minima | Finalidade                    |
|--------------------|---------------|-------------------------------|
| Docker             | 24.0+         | Containerizacao               |
| Docker Compose     | 2.0+          | Orquestracao de containers    |

### Hardware Recomendado

| Recurso   | Minimo     | Recomendado | Observacao                              |
|-----------|------------|-------------|-----------------------------------------|
| CPU       | 2 vCPU     | 4 vCPU      | EasyOCR consome CPU significativo       |
| RAM       | 4 GB       | 8 GB        | Modelo EasyOCR usa ~1.5 GB              |
| Disco     | 20 GB      | 30 GB       | Imagem Docker com PyTorch e pesada      |
| Rede      | 10 Mbps    | 50 Mbps     | Download de streams ao vivo             |

---

## Instalacao Local

### 1. Clonar o repositorio

```bash
git clone https://github.com/Remateweb/OCR.git
cd OCR
```

### 2. Criar ambiente virtual

```bash
python3.11 -m venv venv
source venv/bin/activate
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Instalar FFmpeg e yt-dlp

```bash
# macOS
brew install ffmpeg yt-dlp

# Ubuntu/Debian
sudo apt install ffmpeg
pip install yt-dlp
```

### 5. Executar o servidor

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

O servidor estara acessivel em `http://localhost:8000`.

---

## Deploy em Producao

### Opcao A: Somente IP (sem dominio)

Ideal para teste rapido em VMs na nuvem (GCE, AWS, etc).

```bash
# No servidor
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Reconectar SSH e clonar
git clone https://github.com/Remateweb/OCR.git /opt/ocr-remateweb
cd /opt/ocr-remateweb

mkdir -p data frames
docker compose -f docker-compose.simple.yml up -d --build
```

Acesso: `http://IP_DO_SERVIDOR`

**Importante:** Liberar porta 80 no firewall da cloud provider.

### Opcao B: Com dominio e SSL

Para producao com HTTPS e certificado Let's Encrypt.

```bash
cd /opt/ocr-remateweb
./deploy.sh ocr.seudominio.com seuemail@gmail.com
```

O script executa automaticamente:

1. Substituicao do dominio nos configs do Nginx
2. Criacao dos diretorios de dados e certificados
3. Build e start dos containers (HTTP temporario)
4. Geracao do certificado SSL via Certbot
5. Ativacao da configuracao HTTPS
6. Reinicio do Nginx com SSL

### Arquivos de Deploy

| Arquivo                      | Finalidade                                       |
|------------------------------|--------------------------------------------------|
| `Dockerfile`                 | Imagem da aplicacao (Python 3.11 + FFmpeg)       |
| `docker-compose.yml`         | Stack completa (App + Nginx + Certbot)           |
| `docker-compose.simple.yml`  | Stack simples (App direto na porta 80)           |
| `deploy.sh`                  | Script automatizado de deploy com SSL            |
| `nginx/nginx.conf`           | Configuracao Nginx com SSL e reverse proxy       |
| `nginx/nginx-init.conf`      | Configuracao Nginx HTTP (para gerar certificado) |
| `.dockerignore`              | Exclusao de arquivos desnecessarios no build      |

### Volumes Persistentes

| Volume Host    | Container Path  | Conteudo                          |
|----------------|-----------------|-----------------------------------|
| `./data`       | `/app/data`     | Banco de dados SQLite             |
| `./frames`     | `/app/frames`   | Cache de frames e debug crops     |

---

## Estrutura de Arquivos

```
ocr-remateweb/
|-- server.py                  # Servidor FastAPI (rotas, websocket, OCR loop)
|-- ocr_extractor.py           # Motor OCR (EasyOCR + preprocessamento)
|-- stream_manager.py          # Gerenciador de captura de streams
|-- requirements.txt           # Dependencias Python
|-- Dockerfile                 # Imagem Docker
|-- docker-compose.yml         # Compose com Nginx + SSL
|-- docker-compose.simple.yml  # Compose simples (somente app)
|-- deploy.sh                  # Script de deploy automatizado
|-- static/
|   |-- index.html             # Dashboard principal (listagem de salas)
|   |-- room.html              # Interface da sala (preview, regioes, OCR)
|   |-- login.html             # Tela de login (autenticacao RemateWeb)
|   |-- css/
|   |   |-- style.css          # Estilos globais (tema escuro, glassmorphism)
|   |-- js/
|       |-- app.js             # Logica do frontend (canvas, websocket, OCR)
|-- nginx/
|   |-- nginx.conf             # Config Nginx com HTTPS
|   |-- nginx-init.conf        # Config Nginx HTTP (setup inicial)
|-- frames/                    # Cache de frames por sala (gerado em runtime)
|-- data/                      # Banco de dados SQLite (gerado em runtime)
```

---

## Rotas da API

### Autenticacao

| Metodo | Rota                | Descricao                                        |
|--------|---------------------|--------------------------------------------------|
| POST   | `/api/auth/login`   | Autentica via API RemateWeb e verifica role admin |
| GET    | `/api/auth/check`   | Valida token JWT e verifica expiracao             |

**POST `/api/auth/login`**

```json
// Request
{ "username": "admin@remateweb.com", "password": "senha123" }

// Response 200
{ "status": "ok", "access_token": "eyJ...", "user": { "id": "1", "name": "Admin", "role": "Admin" } }

// Response 401
{ "error": "Usuario ou senha incorretos" }

// Response 403
{ "error": "Acesso negado. Seu perfil (User) nao tem permissao de administrador." }
```

---

### Salas

| Metodo | Rota                           | Descricao                           |
|--------|--------------------------------|-------------------------------------|
| GET    | `/api/rooms`                   | Lista todas as salas                |
| POST   | `/api/rooms`                   | Cria uma nova sala                  |
| GET    | `/api/rooms/{room_id}`         | Detalhes de uma sala                |
| DELETE | `/api/rooms/{room_id}`         | Exclui uma sala e seus dados        |

**POST `/api/rooms`**

```json
// Request
{ "name": "Leilao Nelore Eliza" }

// Response
{ "id": "a1b2c3d4", "name": "Leilao Nelore Eliza" }
```

---

### Stream

| Metodo | Rota                                    | Descricao                           |
|--------|-----------------------------------------|-------------------------------------|
| POST   | `/api/rooms/{room_id}/stream`           | Configura URL do stream             |
| GET    | `/api/rooms/{room_id}/frame`            | Retorna frame atual (JPEG)          |
| GET    | `/api/rooms/{room_id}/stream.mjpeg`     | Stream MJPEG ao vivo (~10fps)       |

**POST `/api/rooms/{room_id}/stream`**

```json
// Request
{ "stream_url": "https://www.youtube.com/watch?v=xxxxx" }

// Response
{ "status": "ok", "stream_type": "youtube", "has_frame": true }
```

**Tipos de stream suportados:**

| Tipo     | Exemplo de URL                              | Resolucao           |
|----------|---------------------------------------------|---------------------|
| YouTube  | `https://youtube.com/watch?v=xxx`           | yt-dlp -> HLS       |
| RTMP     | `rtmp://servidor:1935/live/stream`          | Direto via FFmpeg    |
| HLS      | `https://servidor/stream/index.m3u8`        | Direto via FFmpeg    |
| Direto   | `http://servidor/video.mp4`                 | Direto via FFmpeg    |

---

### Regioes OCR

| Metodo | Rota                                         | Descricao                              |
|--------|----------------------------------------------|----------------------------------------|
| POST   | `/api/rooms/{room_id}/regions`               | Define regioes de extracao             |
| POST   | `/api/rooms/{room_id}/apply-template`        | Aplica um template de regioes          |

**POST `/api/rooms/{room_id}/regions`**

```json
// Request
{
  "regions": [
    { "type": "lote", "label": "Lote", "x": 0.05, "y": 0.85, "width": 0.08, "height": 0.06 },
    { "type": "valor", "label": "Valor", "x": 0.75, "y": 0.85, "width": 0.20, "height": 0.06 },
    { "type": "nome", "label": "Pagamento", "x": 0.30, "y": 0.85, "width": 0.25, "height": 0.06 }
  ]
}
```

As coordenadas sao **relativas** (0.0 a 1.0), proporcionais ao tamanho do frame.

**Tipos de regiao predefinidos:**

| Tipo    | Parser Aplicado          | Allowlist OCR                  |
|---------|--------------------------|--------------------------------|
| `lote`  | Extrai numero do lote    | `0123456789LOTElote`           |
| `valor` | Extrai valor monetario   | `0123456789.,RS$`              |
| `nome`  | Limpa texto              | Todos os caracteres            |
| custom  | Texto bruto              | Todos os caracteres            |

---

### Templates

| Metodo | Rota                           | Descricao                              |
|--------|--------------------------------|----------------------------------------|
| GET    | `/api/templates`               | Lista todos os templates               |
| POST   | `/api/templates`               | Cria um novo template                  |
| DELETE | `/api/templates/{template_id}` | Exclui um template                     |

**POST `/api/templates`**

```json
// Request
{
  "name": "Canal Rural",
  "regions": [
    { "type": "lote", "x": 0.05, "y": 0.85, "width": 0.08, "height": 0.06 }
  ]
}
```

---

### Controle de Extracao

| Metodo | Rota                                    | Descricao                              |
|--------|-----------------------------------------|----------------------------------------|
| POST   | `/api/rooms/{room_id}/start`            | Inicia extracao OCR continua           |
| POST   | `/api/rooms/{room_id}/stop`             | Para a extracao OCR                    |
| POST   | `/api/rooms/{room_id}/interval`         | Altera intervalo entre extracoes       |
| POST   | `/api/rooms/{room_id}/auction-id`       | Define o Auction ID para envio na API  |
| GET    | `/api/rooms/{room_id}/latest`           | Ultimo resultado OCR em memoria        |
| GET    | `/api/rooms/{room_id}/test-ocr`         | Executa OCR uma vez (debug)            |

**POST `/api/rooms/{room_id}/interval`**

```json
{ "interval": 2.0 }
```

**POST `/api/rooms/{room_id}/auction-id`**

```json
{ "auction_id": "12345" }
```

---

### Historico e Relatorios

| Metodo | Rota                                       | Descricao                              |
|--------|---------------------------------------------|----------------------------------------|
| GET    | `/api/rooms/{room_id}/extractions`          | Historico de extracoes (limit=50)      |
| GET    | `/api/rooms/{room_id}/lot-report`           | Relatorio de lotes (sem duplicatas)    |
| POST   | `/api/rooms/{room_id}/lot-report`           | Salva relatorio de lote finalizado     |
| DELETE | `/api/rooms/{room_id}/lot-report`           | Limpa todo o relatorio                 |
| GET    | `/api/rooms/{room_id}/post-log`             | Log de POSTs enviados                  |

---

### Debug

| Metodo | Rota                                             | Descricao                          |
|--------|--------------------------------------------------|------------------------------------|
| GET    | `/api/rooms/{room_id}/debug`                     | Lista imagens de debug             |
| GET    | `/api/rooms/{room_id}/debug/{filename}`          | Serve uma imagem de debug (PNG)    |

Para cada regiao, o sistema salva duas imagens de debug:

- `{tipo}_1_raw.png` -- Crop original da regiao
- `{tipo}_2_processed.png` -- Imagem apos preprocessamento (inversao, upscale)

---

### Monitoramento

| Metodo | Rota                 | Descricao                                         |
|--------|----------------------|---------------------------------------------------|
| GET    | `/api/system/stats`  | Metricas do sistema (CPU, RAM, disco, rede, app)  |

**Response:**

```json
{
  "cpu": { "percent": 45.2, "cores": 4, "freq_mhz": 2400 },
  "memory": { "total": 8589934592, "used": 5368709120, "available": 3221225472, "percent": 62.5 },
  "disk": { "total": 32212254720, "used": 15032385536, "free": 17179869184, "percent": 46.7 },
  "network": { "bytes_sent": 1048576, "bytes_recv": 52428800 },
  "process": { "memory_rss": 157286400, "cpu_percent": 12.3, "pid": 1234 },
  "app": { "uptime_seconds": 3600, "active_ocr_rooms": 2, "active_ws_connections": 3, "total_streams": 2 },
  "system": { "os": "Linux", "os_version": "5.15.0", "hostname": "ocr-server", "python_version": "3.11.9" }
}
```

---

## WebSocket

**Endpoint:** `ws://{host}/ws/{room_id}`

O WebSocket transmite dados em tempo real para o frontend.

### Mensagens Server -> Client

**Extracao OCR:**

```json
{
  "type": "extraction",
  "data": {
    "lote": "15",
    "valor": "125.000",
    "nome": "A Vista",
    "confidence": { "lote": 92, "valor": 88, "nome": 75 }
  },
  "timestamp": "2026-04-08T14:30:00",
  "changed": true
}
```

**Erro de stream:**

```json
{
  "type": "stream_error",
  "message": "A transmissao nao esta mais ao vivo.",
  "raw_error": "Error opening input: ..."
}
```

### Mensagens Client -> Server

| Mensagem | Resposta                     |
|----------|------------------------------|
| `ping`   | `{ "type": "pong" }`        |

---

## Modulos

### server.py

Servidor principal FastAPI. Responsabilidades:

- Rotas HTTP e WebSocket
- Gerenciamento do ciclo de vida das salas
- Loop de extracao OCR assincrono (`ocr_loop`)
- Deteccao de troca de lote (`upsert_lot`)
- Integracao com API RemateWeb (`post_ocr_bid`)
- Alertas Telegram (`send_telegram_alert`)
- Monitoramento do sistema (`system_stats`)

### ocr_extractor.py

Motor de reconhecimento optico de caracteres. Responsabilidades:

- Singleton do EasyOCR Reader (carrega modelo uma vez)
- Preprocessamento de imagens (inversao, upscale para crops pequenos)
- Parsers especializados por tipo de regiao (lote, valor, nome)
- Threshold de confianca (abaixo de 39% retorna "0")
- Geracao de imagens de debug (raw e processed)

**Pipeline de processamento por regiao:**

```
Frame JPEG -> Crop da regiao -> Inversao (se leilao) -> Upscale (se < 80px) -> EasyOCR -> Parser -> Resultado
```

### stream_manager.py

Gerenciador de captura de streams ao vivo. Responsabilidades:

- Deteccao automatica do tipo de stream (YouTube, RTMP, HLS, direto)
- Resolucao de URLs do YouTube via yt-dlp
- Captura de frames via FFmpeg (subprocess)
- Loop de captura em thread separada (nao bloqueia o event loop)
- Persistencia do ultimo frame em disco (fallback apos restart)
- Re-resolucao automatica de URL apos 3 falhas consecutivas

---

## Banco de Dados

SQLite com as seguintes tabelas:

### rooms

| Coluna       | Tipo    | Descricao                           |
|--------------|---------|-------------------------------------|
| id           | TEXT PK | Identificador unico (8 chars)       |
| name         | TEXT    | Nome da sala                        |
| stream_url   | TEXT    | URL do stream configurado           |
| auction_id   | TEXT    | ID do leilao na API RemateWeb       |
| ocr_interval | REAL    | Intervalo entre extracoes (seg)     |
| status       | TEXT    | Estado: idle, running               |
| created_at   | TEXT    | Data de criacao (ISO 8601)          |
| updated_at   | TEXT    | Data de atualizacao (ISO 8601)      |

### regions

| Coluna   | Tipo    | Descricao                              |
|----------|---------|----------------------------------------|
| id       | TEXT PK | Identificador unico                    |
| room_id  | TEXT FK | Referencia a sala                      |
| type     | TEXT    | Tipo: lote, valor, nome, custom        |
| label    | TEXT    | Rotulo exibido na UI                   |
| value    | TEXT    | Chave no payload                       |
| x        | REAL    | Posicao X relativa (0-1)              |
| y        | REAL    | Posicao Y relativa (0-1)              |
| width    | REAL    | Largura relativa (0-1)                |
| height   | REAL    | Altura relativa (0-1)                 |

### extractions

| Coluna       | Tipo       | Descricao                          |
|--------------|------------|------------------------------------|
| id           | INTEGER PK | Auto-incremento                    |
| room_id      | TEXT FK    | Referencia a sala                  |
| data         | TEXT       | JSON com resultado da extracao     |
| extracted_at | TEXT       | Timestamp da extracao (ISO 8601)   |

### lot_history

| Coluna      | Tipo       | Descricao                           |
|-------------|------------|-------------------------------------|
| id          | INTEGER PK | Auto-incremento                    |
| room_id     | TEXT FK    | Referencia a sala                   |
| lot_number  | TEXT       | Numero do lote                      |
| started_at  | TEXT       | Inicio da extracao do lote          |
| ended_at    | TEXT       | Fim / ultima atualizacao            |
| final_value | TEXT       | Ultimo valor extraido               |
| bid_count   | INTEGER    | Quantidade de lances detectados     |
| extra_data  | TEXT       | JSON com dados adicionais           |

### bid_history

| Coluna      | Tipo       | Descricao                           |
|-------------|------------|-------------------------------------|
| id          | INTEGER PK | Auto-incremento                    |
| room_id     | TEXT FK    | Referencia a sala                   |
| lot_number  | TEXT       | Numero do lote                      |
| value       | TEXT       | Valor do lance                      |
| payload     | TEXT       | JSON do payload completo            |
| captured_at | TEXT       | Timestamp da captura (ISO 8601)     |

### post_log

| Coluna    | Tipo       | Descricao                            |
|-----------|------------|--------------------------------------|
| id        | INTEGER PK | Auto-incremento                     |
| room_id   | TEXT FK    | Referencia a sala                    |
| old_lot   | TEXT       | Lote anterior                        |
| new_lot   | TEXT       | Novo lote detectado                  |
| bid_count | INTEGER    | Lances do lote anterior              |
| timestamp | TEXT       | Timestamp do POST (ISO 8601)         |

### region_templates

| Coluna     | Tipo    | Descricao                             |
|------------|---------|---------------------------------------|
| id         | TEXT PK | Identificador unico                   |
| name       | TEXT    | Nome do template                      |
| regions    | TEXT    | JSON com array de regioes             |
| created_at | TEXT    | Data de criacao (ISO 8601)            |

---

## Monitoramento do Sistema

O dashboard principal (`index.html`) exibe metricas do servidor em tempo real via polling a cada 3 segundos:

- **CPU** -- Percentual de uso, numero de cores, frequencia
- **Memoria RAM** -- Uso atual, total, percentual
- **Disco** -- Espaco usado, livre, percentual
- **Rede** -- Bytes enviados e recebidos (acumulativo)
- **Processo OCR** -- Memoria RSS do processo Python
- **Uptime** -- Tempo de execucao do servidor
- **Salas Ativas** -- Quantidade de salas com OCR rodando
- **Conexoes WebSocket** -- Clientes conectados em tempo real

Os gauges mudam de cor dinamicamente conforme o nivel de uso:

| Nível      | Cor       | Faixa     |
|------------|-----------|-----------|
| Normal     | Verde     | 0-49%     |
| Atencao    | Amarelo   | 50-74%    |
| Alerta     | Laranja   | 75-89%    |
| Critico    | Vermelho  | 90-100%   |

---

## Integracoes Externas

### API RemateWeb

Quando um dado muda e existe um `auction_id` configurado na sala, o sistema envia automaticamente:

```
POST https://test.api-net9.remateweb.com/api/ocr/bid
```

```json
{
  "apiKey": "0c18ab41-eb23-4782-8e3f-34582fad10b6",
  "auctionId": 12345,
  "lotNumber": "15",
  "value": 125000.0
}
```

Payloads duplicados sao filtrados automaticamente (nao reenvia se lote e valor nao mudaram).

### Telegram

Alertas automaticos sao enviados via bot do Telegram quando:

- Stream fica offline por mais de 10 segundos
- Stream volta ao ar apos queda

Cooldown de 5 minutos entre alertas da mesma sala para evitar spam.

### Autenticacao

O login e feito via proxy para a API RemateWeb (`/token`). O JWT retornado e decodificado no servidor para verificar se o usuario possui role `Admin`. Somente administradores podem acessar o sistema.

---

## Configuracao

### Variaveis de Ambiente

| Variavel  | Padrao          | Descricao                    |
|-----------|-----------------|------------------------------|
| `DB_PATH` | `ocr_rooms.db`  | Caminho do banco SQLite      |

### Constantes no server.py

| Constante             | Descricao                                |
|-----------------------|------------------------------------------|
| `TELEGRAM_BOT_TOKEN`  | Token do bot Telegram para alertas       |
| `TELEGRAM_CHAT_ID`    | Chat ID do grupo/usuario Telegram        |
| `TELEGRAM_COOLDOWN`   | Intervalo minimo entre alertas (seg)     |
| `REMATEWEB_API_URL`   | Endpoint da API RemateWeb                |
| `REMATEWEB_API_KEY`   | Chave de autenticacao da API             |

---

## Troubleshooting

### Stream do YouTube nao conecta

```
Erro: "Sign in to confirm you're not a bot"
```

O YouTube pode exigir autenticacao para certas lives. Solucoes:

1. Use uma URL direta HLS/RTMP em vez do YouTube
2. Crie um arquivo `cookies.txt` com cookies do YouTube autenticado:
   ```bash
   yt-dlp --cookies-from-browser chrome --cookies cookies.txt "https://youtube.com"
   ```

### OCR retorna valores incorretos

1. Acesse a sala e clique em "Frame" para atualizar o preview
2. Verifique as "Debug Crops" para ver o que o OCR esta processando
3. Ajuste o posicionamento das regioes para cobrir apenas o texto desejado
4. Para textos muito pequenos, aumente a area da regiao

### Servidor sem memoria (OOM)

O EasyOCR com PyTorch consome ~1.5 GB de RAM. Recomendacoes:

- Use uma VM com no minimo 4 GB de RAM
- Reduza o numero de salas simultaneas
- Aumente o intervalo de OCR (ex: 3-5 segundos)
- Monitore o uso pelo dashboard na pagina principal

### Container Docker nao inicia

```bash
# Verificar logs
docker compose logs ocr-app

# Verificar espaco em disco
df -h

# Rebuild sem cache
docker compose build --no-cache
docker compose up -d
```

### Porta 80 nao acessivel na GCE

1. Console GCP -> VPC Network -> Firewall rules
2. Create rule: allow-http, TCP:80, Source: 0.0.0.0/0
3. Ou marque "Permitir trafego HTTP" na configuracao da VM

---

## Dependencias Python

| Pacote           | Finalidade                                   |
|------------------|----------------------------------------------|
| fastapi          | Framework web assincrono                     |
| uvicorn          | Servidor ASGI                                |
| easyocr          | Motor OCR baseado em deep learning           |
| opencv-python    | Processamento de imagens                     |
| numpy            | Operacoes numericas (dependencia do OpenCV)  |
| httpx            | Cliente HTTP assincrono (Telegram, API)      |
| requests         | Cliente HTTP sincrono                        |
| python-multipart | Upload de arquivos (FastAPI)                 |
| yt-dlp           | Resolucao de URLs do YouTube                 |
| aiosqlite        | SQLite assincrono                            |
| psutil           | Monitoramento de recursos do sistema         |

---

## Licenca

Uso interno -- RemateWeb.
