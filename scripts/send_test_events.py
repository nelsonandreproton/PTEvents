"""Send one example event of each type to Telegram to verify formatting."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
# Load .env so that ${VAR} placeholders in settings.yaml resolve correctly
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

from bot.main import load_settings
from bot.notifier import Notifier
from models.event import Event, EventType, Severity

NOW = datetime.now(timezone.utc)
SOON = NOW + timedelta(hours=3)
HOME_LAT, HOME_LON = 38.8097, -9.2518

EXAMPLES = [
    Event(
        id="test_fire",
        source="fogos",
        type=EventType.FIRE,
        title="Incêndio ativo em Sintra",
        description="Incêndio em curso. Área ardida estimada: 5 ha. Meios aéreos no local.",
        lat=38.79, lon=-9.37, severity=Severity.HIGH,
        status="active", started_at=NOW - timedelta(minutes=25),
        url="https://fogos.pt",
    ),
    Event(
        id="test_earthquake",
        source="ipma",
        type=EventType.EARTHQUAKE,
        title="Sismo M2.8 — Amadora",
        description="Sismo de magnitude 2.8 ML. Profundidade: 8 km. Sentido na região de Lisboa.",
        lat=38.75, lon=-9.23, severity=Severity.MEDIUM,
        status="active", started_at=NOW - timedelta(minutes=5),
        url="https://www.ipma.pt/pt/geofisica/sismicidade/",
    ),
    Event(
        id="test_storm",
        source="ipma",
        type=EventType.STORM,
        title="Aviso Laranja — Tempestade",
        description="Vento com rajadas até 100 km/h. Ondulação até 5 m na costa.",
        lat=38.81, lon=-9.25, severity=Severity.HIGH,
        status="active", started_at=NOW, ends_at=SOON,
        url="https://www.ipma.pt/pt/otempo/prev-sam/",
    ),
    Event(
        id="test_congestion",
        source="transit",
        type=EventType.CONGESTION,
        title="Congestionamento A5 sentido Lisboa",
        description="Tráfego intenso entre Cascais e o nó de Estoril. Demora estimada: 20 min.",
        lat=38.70, lon=-9.39, severity=Severity.MEDIUM,
        status="active", started_at=NOW - timedelta(minutes=10),
        url="https://www.waze.com",
    ),
    Event(
        id="test_road_closure",
        source="transit",
        type=EventType.ROAD_CLOSURE,
        title="Corte na EN249 — Sintra",
        description="Via cortada devido a acidente. Desvio pela EN9.",
        lat=38.80, lon=-9.38, severity=Severity.HIGH,
        status="active", started_at=NOW - timedelta(minutes=40),
        url="https://www.waze.com",
    ),
    Event(
        id="test_air_quality",
        source="air_quality",
        type=EventType.AIR_QUALITY,
        title="Qualidade do Ar — Fraco em Odivelas",
        description="Índice IQAr: Fraco. Poluente dominante: NO2. Valor: 85 µg/m³.",
        lat=38.79, lon=-9.18, severity=Severity.HIGH,
        status="active", started_at=NOW,
        url="https://qualar.apambiente.pt",
    ),
    Event(
        id="test_strike",
        source="greves",
        type=EventType.STRIKE,
        title="Greve nos transportes rodoviários — 12 Mai",
        description="Pré-aviso de greve dos motoristas de transporte público. Serviços mínimos garantidos.",
        lat=38.71, lon=-9.13, severity=Severity.HIGH,
        status="active", started_at=NOW,
        url="https://www.dgert.gov.pt",
    ),
    Event(
        id="test_obras",
        source="obras",
        type=EventType.PLANNED_WORKS,
        title="Obras de pavimentação — Rua da Liberdade, Sintra",
        description="Intervenção de requalificação do arruamento. Trânsito condicionado.",
        lat=38.80, lon=-9.38, severity=Severity.LOW,
        status="active", started_at=NOW - timedelta(days=2),
        url="https://cm-sintra.pt",
    ),
    Event(
        id="test_evento",
        source="eventos",
        type=EventType.EVENT_CLOSURE,
        title="Feira Medieval de Sintra — 10 Mai",
        description="Evento cultural no centro histórico. Condicionamento de trânsito na zona antiga.",
        lat=38.80, lon=-9.39, severity=Severity.LOW,
        status="active", started_at=NOW + timedelta(days=3),
        url="https://www.eventbrite.pt",
    ),
    Event(
        id="test_firms",
        source="nasa_firms",
        type=EventType.FIRE,
        title="Foco de calor detetado por satélite (2026-05-07)",
        description="Deteção VIIRS satélite (confiança: H). FRP: 145.3 MW. Temp. brilho: 342 K.",
        lat=38.77, lon=-9.30, severity=Severity.CRITICAL,
        status="active", started_at=NOW - timedelta(minutes=90),
        url="https://firms.modaps.eosdis.nasa.gov/map/",
    ),
]


async def main() -> None:
    settings = load_settings()
    telegram_cfg = settings.get("telegram", {})
    notifier = Notifier(token=telegram_cfg["token"], chat_id=telegram_cfg["chat_id"])

    location_name = settings.get("location", {}).get("name", "Casa")

    print(f"Sending {len(EXAMPLES)} test events to Telegram...")
    for event in EXAMPLES:
        ok = await notifier.send_event(event, distance_km=2.4, location_name=location_name)
        status = "✅" if ok else "❌"
        print(f"  {status} {event.type.value} ({event.source})")
        await asyncio.sleep(0.5)

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
