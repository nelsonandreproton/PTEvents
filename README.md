# PTEvents — Alerta Local

Monitor de eventos disruptivos num raio configurável. Envia notificações Telegram em tempo real e serve um dashboard web com mapa interativo.

## Arquitetura

PTEvents corre como **scheduler + servidor web** — não tem bot Telegram próprio.
As notificações são enviadas pelo `Notifier` (push direto via Bot API).
Os comandos Telegram (`/alertas`, `/alertas_tipos`, `/alertas_severidade`) são registados no **GarminBot**, que chama a API REST do PTEvents por HTTP.

```
GarminBot ──HTTP──► PTEvents REST API (:8085)
                         │
                    AlertScheduler
                         │
              ┌──────────┴──────────┐
           Collectors           Notifier
       (IPMA, Fogos, ...)    (push Telegram)
```

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
- aiohttp 3.9 (web dashboard + REST API)
- APScheduler 3.x
- httpx (async HTTP nos collectors)
- SQLite (deduplicação com TTL)
- tenacity (retry com backoff exponencial)
- defusedxml (parse RSS seguro)
- Docker Compose

## Estrutura

```
PTEvents/
├── bot/          main.py, scheduler.py, notifier.py, geo.py, preferences.py, web.py
├── collectors/   base.py, ipma.py, fogos.py, transit.py,
│                 air_quality.py, greves.py, obras.py,
│                 eventos.py, nasa_firms.py, edp.py
├── models/       event.py, db.py
├── config/       settings.yaml
└── tests/        100 testes (pytest)
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
  radius_km: 5
  name: "Casa"

filters:
  min_severity: LOW
  enabled_types: null   # null = todos os 30 tipos ativos
```

## Arranque

```bash
docker compose up -d
```

O serviço expõe a porta `8085` (configurável via `PORT` env var).

## API REST

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/ptevents/api/events` | Eventos ativos (max 200) |
| `GET` | `/ptevents/api/status` | Contagem + filtros ativos |
| `GET` | `/ptevents/api/filters` | Filtros atuais |
| `PUT` | `/ptevents/api/filters` | Atualizar `min_severity` e/ou `enabled_types` |
| `DELETE` | `/ptevents/api/events/{id}` | Dispensar evento |
| `GET` | `/ptevents` | Dashboard web |

## Comandos Telegram (via GarminBot)

| Comando | Descrição |
|---------|-----------|
| `/alertas` | Estado atual: eventos ativos, severidade, tipos |
| `/alertas_tipos` | Ativar/desativar tipos de eventos (menu paginado) |
| `/alertas_severidade` | Definir severidade mínima das notificações |

As preferências são guardadas em `config/settings.yaml` e aplicadas no próximo tick do scheduler — sem necessidade de reinício.

## Filtros

- **`min_severity`**: `LOW` / `MEDIUM` / `HIGH` / `CRITICAL`
- **`enabled_types`**: lista de tipos ativos, ou `null` para todos
- Por coletor, é possível definir `excluded_types` em `settings.yaml` para exclusões estáticas (ex: ignorar CONGESTION no coletor de trânsito)

## Testes

```bash
pip install -r requirements.txt
pytest tests/
```

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|----------|------------|-----------|
| `TELEGRAM_BOT_TOKEN` | Sim | Token do @BotFather (para envio de notificações) |
| `TELEGRAM_CHAT_ID` | Sim | ID do chat para notificações |
| `PORT` | Não | Porta do servidor web (default: 8080) |
| `HERE_API_KEY` | Não | Trânsito HERE (fallback) |
| `TOMTOM_API_KEY` | Não | Trânsito TomTom (fallback) |
| `NASA_FIRMS_KEY` | Não | Satélite incêndios VIIRS |
| `EVENTBRITE_TOKEN` | Não | Token privado Eventbrite |

## Notas

- **Waze** não requer chave API e é o provider de trânsito primário.
- **Eventbrite** requer um *Private Token* — obtido em [eventbrite.com/account-settings/apps](https://www.eventbrite.com/account-settings/apps).
- **E-REDES** não expõe API pública — coletor em stub.
- Todas as chaves opcionais: se não configuradas, o coletor respetivo é ignorado silenciosamente.
- PTEvents nunca faz polling Telegram — partilha o token apenas para envio (push). O bot polling é exclusivo do GarminBot.
