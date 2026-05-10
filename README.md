# PTEvents — Alerta Local

Bot Telegram que monitoriza eventos disruptivos num raio configurável e envia notificações em tempo real.

## Fontes de dados

| Fonte | Dados | Intervalo |
|-------|-------|-----------|
| IPMA | Avisos meteorológicos + sismos | 10 min |
| Fogos.pt | Incêndios ativos | 5 min |
| Waze / TomTom / HERE | Incidentes de trânsito (waterfall) | 3 min |
| APA QualAr | Índice de qualidade do ar (IQAr) | 30 min |
| DGERT | Greves e pré-avisos | 60 min |
| Obras (Odivelas/Sintra/Amadora) | Obras na via pública | 6 h |
| Eventos (Odivelas/Eventbrite) | Eventos públicos locais | 60 min |
| NASA FIRMS VIIRS | Focos de calor por satélite | 10 min |

## Stack

- Python 3.11
- python-telegram-bot 20.7
- APScheduler 3.x
- httpx (async HTTP)
- SQLite (deduplicação com TTL)
- tenacity (retry com backoff exponencial)
- defusedxml (parse RSS seguro)
- Docker Compose

## Estrutura

```
PTEvents/
├── bot/          main.py, scheduler.py, notifier.py, geo.py, keyboards.py, preferences.py
├── collectors/   base.py, ipma.py, fogos.py, transit.py,
│                 air_quality.py, greves.py, obras.py,
│                 eventos.py, nasa_firms.py, edp.py
├── models/       event.py, db.py
├── config/       settings.yaml
└── tests/        128 testes (pytest)
```

## Configuração

```bash
cp .env.example .env
# editar .env com tokens e chaves API
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
| `/start` | Boas-vindas e lista de comandos |
| `/status` | Eventos ativos (últimos 20) |
| `/ping` | Health check |
| `/radius <km>` | Ajusta raio de monitorização (temporário) |
| `/types` | Ativar/desativar tipos de eventos (menu interativo) |
| `/severity` | Definir severidade mínima das notificações |

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
| `HERE_API_KEY` | Não | Trânsito HERE (fallback) |
| `TOMTOM_API_KEY` | Não | Trânsito TomTom (fallback) |
| `NASA_FIRMS_KEY` | Não | Satélite incêndios VIIRS |
| `EVENTBRITE_TOKEN` | Não | Token privado Eventbrite (dashboard → API Keys → Private token) |

## Notas

- **Waze** não requer chave API e é o provider de trânsito primário.
- **Eventbrite** requer um *Private Token* (não o app key/client secret) — obtido em [eventbrite.com/account-settings/apps](https://www.eventbrite.com/account-settings/apps).
- **E-REDES** (antiga EDP Distribuição) não expõe API pública — coletor em stub.
- Todas as chaves opcionais: se não configuradas, o coletor respetivo é ignorado silenciosamente.
