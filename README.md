# PTEvents — Alerta Local

Bot Telegram que monitoriza eventos disruptivos num raio configurável e envia notificações em tempo real.

## Fontes de dados (Fase 1)

| Fonte | Dados | Intervalo |
|-------|-------|-----------|
| IPMA | Avisos meteorológicos + sismos | 10 min |
| Fogos.pt | Incêndios ativos | 5 min |

## Stack

- Python 3.11
- python-telegram-bot 20+
- APScheduler 3.x
- httpx (async HTTP)
- SQLite (deduplicação com TTL)
- Docker Compose

## Estrutura

```
PTEvents/
├── bot/          main.py, scheduler.py, notifier.py, geo.py
├── collectors/   base.py, ipma.py, fogos.py
├── models/       event.py, db.py
├── config/       settings.yaml
└── tests/        test_geo.py, test_dedup.py
```

## Configuração

```bash
cp .env.example .env
# editar .env com TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
```

Editar `config/settings.yaml` com a localização pretendida:

```yaml
location:
  lat: 38.7169
  lon: -9.1399
  radius_km: 10
  name: "Casa"
```

## Arranque

```bash
docker compose up -d
```

## Comandos do bot

| Comando | Descrição |
|---------|-----------|
| `/start` | Boas-vindas e estado |
| `/status` | Eventos ativos nas últimas horas |
| `/ping` | Health check |

## Testes

```bash
pip install -r requirements.txt
pytest tests/
```

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|----------|------------|-----------|
| `TELEGRAM_BOT_TOKEN` | Sim | Token do @BotFather |
| `TELEGRAM_CHAT_ID` | Sim | ID do chat para notificações |
| `HERE_API_KEY` | Não | Trânsito HERE (Fase 2) |
| `TOMTOM_API_KEY` | Não | Trânsito TomTom (Fase 2) |
| `NASA_FIRMS_KEY` | Não | Satélite incêndios (Fase 3) |
