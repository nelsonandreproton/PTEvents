# 🚨 Alerta Local — Bot Telegram de Eventos Disruptivos
> Plano de Projeto para Claude Code

---

## 🎯 Visão Geral

Bot Telegram que monitoriza continuamente múltiplas fontes de dados e notifica o utilizador quando ocorre qualquer evento disruptivo num raio de **10 km** de uma localização configurável.

**Stack:** Python 3.11+ · SQLite · APScheduler · python-telegram-bot · Docker

---

## 📁 Estrutura do Projeto

```
alerta-local/
├── bot/
│   ├── main.py                  # Entrypoint + Telegram bot
│   ├── scheduler.py             # APScheduler — ciclos de polling
│   ├── geo.py                   # Haversine + filtro de raio
│   ├── dedup.py                 # Deduplicação de eventos
│   └── notifier.py              # Formatar e enviar mensagem Telegram
├── collectors/
│   ├── base.py                  # Classe abstrata BaseCollector
│   ├── ipma.py                  # IPMA — avisos + sismos + risco incêndio
│   ├── fogos.py                 # Fogos.pt / Wildfire.pt — incêndios
│   ├── transit.py               # Trânsito (Waze scrape / HERE free)
│   ├── infra.py                 # Cortes energia (EDP), água, telecom
│   ├── greves.py                # Greves (scrape CGTP/UGT/Governo)
│   └── obras.py                 # Obras via pública (dados.gov.pt)
├── models/
│   ├── event.py                 # Dataclass Event normalizado
│   └── db.py                   # SQLite — persistência + dedup
├── config/
│   └── settings.yaml            # Lat/long, raio, tokens, intervalos
├── tests/
│   ├── test_geo.py
│   ├── test_collectors.py
│   └── test_dedup.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## 🗂️ Modelo de Dados Central

Todos os collectors normalizam para este formato:

```python
@dataclass
class Event:
    id: str                  # hash(source + external_id)
    source: str              # "ipma", "fogos", "transit", ...
    type: EventType          # enum — ver abaixo
    title: str               # título curto
    description: str         # descrição completa
    lat: float
    lon: float
    severity: Severity       # LOW / MEDIUM / HIGH / CRITICAL
    status: str              # "active", "resolved", "planned"
    started_at: datetime
    ends_at: datetime | None
    url: str | None          # link para mais info
    raw: dict                # payload original
```

### EventType (enum)

| Categoria | Tipos |
|-----------|-------|
| 🔥 Fogo/Proteção Civil | `FIRE`, `CIVIL_PROTECTION`, `EVACUATION` |
| 🌦️ Meteorologia | `STORM`, `WIND`, `RAIN`, `HEAT`, `COLD`, `FLOOD`, `DROUGHT` |
| 🌍 Geologia | `EARTHQUAKE`, `TSUNAMI`, `LANDSLIDE` |
| 🚗 Trânsito | `ACCIDENT`, `ROAD_CLOSURE`, `CONGESTION`, `ROADWORK` |
| ⚡ Infraestrutura | `POWER_OUTAGE`, `WATER_OUTAGE`, `GAS_LEAK`, `TELECOM` |
| 🚌 Transportes Públicos | `STRIKE`, `SERVICE_DISRUPTION`, `DELAY` |
| 🏗️ Planeado | `PLANNED_WORKS`, `EVENT_CLOSURE`, `SCHEDULED_MAINTENANCE` |
| 💨 Ambiente | `AIR_QUALITY`, `FIRE_RISK`, `UV_ALERT` |

---

## 📡 Collectors — Fontes de Dados

### 1. IPMA (obrigatório · grátis · oficial)

**API base:** `https://api.ipma.pt/open-data/`

| Endpoint | Dados | Intervalo |
|----------|-------|-----------|
| `/forecast/warnings/warnings_www.json` | Avisos meteorológicos ativos | 10 min |
| `/observation/seismic/sismicidade.json` | Sismos últimas 24h | 5 min |
| `/forecast/fire-risk/rcm-d0.json` | Risco de incêndio hoje | 1h |
| `/observation/surface/obs-surface.geojson` | Observações meteorológicas | 10 min |

**Geo-match:** avisos por distrito/concelho → mapear para lat/long centróides (ficheiro incluído no projeto)

---

### 2. Fogos / Wildfire (obrigatório · grátis)

**Wildfire.pt** (sem auth):
```
GET https://api.fogos.pt/v2/incidents/active
```
Campos relevantes: `lat`, `lng`, `location`, `status`, `natureza`, `meios`

**NASA FIRMS** (fallback, grátis com registo):
```
GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/{API_KEY}/VIIRS_SNPP_NRT/{bbox}/1
```
Útil para deteção satélite independente.

---

### 3. Trânsito (múltiplas estratégias)

**Opção A — HERE Maps (grátis até 250k req/mês):**
```
GET https://data.traffic.hereapi.com/v7/incidents
    ?apiKey={KEY}&in=circle:{lat},{lon};r=10000
```

**Opção B — TomTom Traffic (grátis 2500 req/dia):**
```
GET https://api.tomtom.com/traffic/services/5/incidentDetails
    ?key={KEY}&bbox={bbox}&fields={...}
```

**Opção C — Waze scrape (sem auth, comportamento de browser):**
```
GET https://www.waze.com/live-map/api/georss
    ?bottom={lat_min}&top={lat_max}&left={lon_min}&right={lon_max}
    &types=alerts,traffic&ma=600
```

**Recomendação:** começar com Waze (zero custo), HERE como fallback.

---

### 4. EDP Outages — Cortes de Energia

**EDP Distribuição** tem mapa de cortes (scraping necessário):
```
GET https://www.edpdistribuicao.pt/pt-PT/Pages/interruptions.aspx
```
Alternativa: monitorizar RSS/sitemap da EDP Distribuição.

Nota: não existe API pública. Usar Playwright headless ou BeautifulSoup.

---

### 5. Greves

Fontes a monitorizar (RSS/scrape):
- `https://www.dgert.gov.pt/greves` (Governo · oficial)
- Portal da ANSR para greves que afetam transportes
- RSS de jornais filtrado por keyword "greve"

Parser: extrair data, setor afetado, região → geocodificar com Nominatim se necessário.

---

### 6. Obras na Via Pública

**dados.gov.pt:**
```
GET https://dados.gov.pt/api/1/datasets/?q=obras+via+publica&format=json
```
Depois fetch ao CSV/GeoJSON de cada dataset relevante.

**Lisboa específico:**
```
GET https://geodados-cml.hub.arcgis.com/datasets/obras-em-curso
```
(ArcGIS open data — várias câmaras têm portais semelhantes)

---

### 7. Qualidade do Ar

**APA (Agência Portuguesa do Ambiente) — grátis:**
```
GET https://rea.apambiente.pt/content/dados-horarios-da-qualidade-do-ar
```
Índice IQAr por estação → interpolação para lat/long do utilizador.

---

### 8. Eventos Planeados (festas, maratonas, cortejo)

- Câmara Municipal respetiva (scrape/RSS)
- Eventbrite API (free tier) filtrado por localização
- Google Events scrape (não oficial mas funcional)

---

## ⚙️ Arquitetura do Sistema

```
┌─────────────────────────────────────────────────────┐
│                    SCHEDULER                         │
│  (APScheduler — intervalos por collector)           │
└──────────┬──────────────────────────────────────────┘
           │ dispara
    ┌──────▼──────┐
    │  COLLECTORS  │  (cada um independente, async)
    │  ipma        │
    │  fogos       │
    │  transit     │
    │  infra       │
    │  greves      │
    │  obras       │
    └──────┬───────┘
           │ lista de Event[]
    ┌──────▼──────────┐
    │  GEO FILTER      │  Haversine — descarta fora de 10km
    └──────┬───────────┘
           │
    ┌──────▼──────────┐
    │  DEDUP ENGINE    │  hash(source+id) → SQLite
    │                  │  TTL por tipo de evento
    └──────┬───────────┘
           │ eventos novos apenas
    ┌──────▼──────────┐
    │  SEVERITY ROUTER │  filtra por severity mínima configurada
    └──────┬───────────┘
           │
    ┌──────▼──────────┐
    │  NOTIFIER        │  formata + envia Telegram
    └─────────────────┘
```

---

## 🔧 Configuração (`settings.yaml`)

```yaml
location:
  lat: 38.7169  # substituir pela tua lat
  lon: -9.1399  # substituir pela tua lon
  radius_km: 10
  name: "Casa"

telegram:
  token: "${TELEGRAM_BOT_TOKEN}"
  chat_id: "${TELEGRAM_CHAT_ID}"

filters:
  min_severity: LOW          # LOW / MEDIUM / HIGH / CRITICAL
  quiet_hours:
    enabled: true
    start: "23:00"
    end: "07:00"
    except_severity: CRITICAL  # CRITICAL passa sempre

collectors:
  ipma:
    enabled: true
    interval_minutes: 10
  fogos:
    enabled: true
    interval_minutes: 5
  transit:
    enabled: true
    interval_minutes: 3
    provider: "waze"        # waze / here / tomtom
  greves:
    enabled: true
    interval_minutes: 60
  obras:
    enabled: true
    interval_minutes: 360
  air_quality:
    enabled: true
    interval_minutes: 30

api_keys:
  here: "${HERE_API_KEY}"
  tomtom: "${TOMTOM_API_KEY}"
  nasa_firms: "${NASA_FIRMS_KEY}"

dedup:
  ttl_hours:
    FIRE: 2
    STORM: 6
    EARTHQUAKE: 24
    ACCIDENT: 1
    ROAD_CLOSURE: 4
    POWER_OUTAGE: 4
    STRIKE: 24
    PLANNED_WORKS: 168    # 1 semana
```

---

## 📲 Formato das Notificações Telegram

```
🔴 INCÊNDIO — CRÍTICO
📍 Sintra (2.3 km de casa)
🕐 Detetado há 12 min

Incêndio florestal ativo com 3 meios terrestres e 1 aéreo.
Frente ativa a NE da localização.

🔗 fogos.pt/ocorrencia/12345
```

```
🟡 AVISO METEOROLÓGICO — MÉDIO
📍 Distrito de Lisboa
🕐 Válido até 23 Jan 18:00

Aviso amarelo de vento forte — rajadas até 70 km/h.
Cuidados recomendados em zonas expostas.

🔗 ipma.pt/avisos
```

```
🔵 CORTE DE TRÂNSITO — BAIXO (PLANEADO)
📍 A5 — Km 12 (4.7 km de casa)
🕐 27 Jan 22:00 → 28 Jan 06:00

Obras noturnas de pavimentação. Faixa direita cortada.
Desvio pela EN117.
```

---

## 🧪 Testes

```python
# test_geo.py
def test_within_radius():
    assert is_within_radius(38.720, -9.145, home_lat=38.717, home_lon=-9.139, radius_km=10)

def test_outside_radius():
    assert not is_within_radius(38.800, -9.200, home_lat=38.717, home_lon=-9.139, radius_km=10)

# test_dedup.py
def test_same_event_not_duplicated():
    db = EventDB(":memory:")
    e = make_test_event(id="abc123")
    assert db.is_new(e) == True
    db.save(e)
    assert db.is_new(e) == False
```

---

## 🐳 Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "bot/main.py"]
```

```yaml
# docker-compose.yml
services:
  bot:
    build: .
    restart: unless-stopped
    volumes:
      - ./data:/app/data      # SQLite persistente
      - ./config:/app/config  # settings.yaml
    env_file: .env
```

---

## 🚀 Fases de Implementação

### Fase 1 — Core (implementar primeiro)
- [ ] Estrutura de projeto + `Event` dataclass
- [ ] `geo.py` com Haversine
- [ ] SQLite dedup com TTL
- [ ] Telegram notifier básico
- [ ] Collector IPMA (avisos + sismos)
- [ ] Collector Fogos/Wildfire
- [ ] Scheduler com APScheduler
- [ ] `settings.yaml` + variáveis de ambiente
- [ ] Docker Compose funcional

### Fase 2 — Trânsito & Infraestrutura
- [ ] Collector Waze (scraping)
- [ ] Collector HERE/TomTom (API)
- [ ] Collector EDP outages (scraping)
- [ ] Collector qualidade do ar (APA)
- [ ] Quiet hours + filtro severity
- [ ] Testes unitários geo + dedup

### Fase 3 — Fontes Adicionais
- [ ] Collector greves (DGERT scrape)
- [ ] Collector obras (dados.gov.pt + câmaras)
- [ ] Collector eventos planeados
- [ ] NASA FIRMS como fallback incêndios
- [ ] Comando `/status` no bot (listar eventos ativos)
- [ ] Comando `/radius 15` (ajustar raio on-the-fly)

### Fase 4 — Polimento
- [ ] Rate limiting por fonte
- [ ] Retry com backoff exponencial
- [ ] Logging estruturado (JSON)
- [ ] Health check endpoint
- [ ] Dashboard HTML simples (opcional)
- [ ] Alertas de falha de collector

---

## 📦 requirements.txt

```
python-telegram-bot==20.7
APScheduler==3.10.4
httpx==0.26.0          # async HTTP
beautifulsoup4==4.12.3
playwright==1.41.0     # para scraping JS-heavy
pyyaml==6.0.1
geopy==2.4.1           # Haversine + geocoding
SQLAlchemy==2.0.25
pydantic==2.5.3
python-dotenv==1.0.0
structlog==24.1.0
```

---

## 🔑 API Keys Necessárias (todas grátis)

| Serviço | Registo | Limite gratuito |
|---------|---------|-----------------|
| NASA FIRMS | [firms.modaps.eosdis.nasa.gov](https://firms.modaps.eosdis.nasa.gov/api/) | ilimitado (razoável) |
| HERE Maps | [developer.here.com](https://developer.here.com) | 250k req/mês |
| TomTom | [developer.tomtom.com](https://developer.tomtom.com) | 2.500 req/dia |
| Telegram Bot | [@BotFather](https://t.me/BotFather) | gratuito |

**Sem key necessária:** IPMA, Fogos.pt/Wildfire.pt, Waze (scrape), dados.gov.pt, APA

---

## ⚡ Prompt de Arranque para Claude Code

```
Implementa o projeto "alerta-local" conforme o plano em alerta-local-bot.md.

Começa pela Fase 1:
1. Cria a estrutura de pastas
2. Implementa models/event.py com o dataclass Event e os enums EventType e Severity
3. Implementa geo.py com a função is_within_radius usando Haversine
4. Implementa models/db.py com SQLite para deduplicação com TTL por tipo
5. Implementa collectors/base.py com a classe abstrata BaseCollector
6. Implementa collectors/ipma.py usando a API pública do IPMA
7. Implementa collectors/fogos.py usando api.fogos.pt/v2/incidents/active
8. Implementa bot/notifier.py com formatação de mensagem Telegram
9. Implementa bot/scheduler.py com APScheduler
10. Implementa bot/main.py como entrypoint
11. Cria config/settings.yaml com os valores default
12. Cria Dockerfile + docker-compose.yml
13. Cria testes para geo.py e db.py

Usa Python 3.11, async/await onde aplicável, httpx para HTTP.
Localização de teste: lat=38.7169, lon=-9.1399 (Lisboa).
```
