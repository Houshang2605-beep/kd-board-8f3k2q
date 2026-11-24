# Datei: btc_daten.py | Ort: tools/btc_daten.py (Repo) | neu
"""
Bitcoin-Tagesdaten für die BTC-Zyklus-Ansicht des Dashboards.

Quelle: Coin Metrics Community API (kostenlos, ohne Schlüssel)
  PriceUSD       Tagesschlusskurs in US-Dollar
  CapMrktCurUSD  Marktkapitalisierung
  CapMVRVCur     MVRV = Marktkapitalisierung / realisierte Kapitalisierung

MVRV Z-Score nach der üblichen Definition (LookIntoBitcoin):
  Z = (Marktkap. − realisierte Kap.) / Standardabweichung der Marktkap.
  realisierte Kap. = Marktkap. / MVRV
  Standardabweichung über die gesamte Historie bis zum jeweiligen Tag.
Andere Anbieter rechnen teils leicht anders; die Werte können abweichen.

Ausgabe: data/btc/btc_tag.json
  {"stand", "quelle", "t": [Unix-Sekunden], "p": [Kurs USD], "z": [Z-Score oder null], "mvrv": [...]}
"""
import json
import math
import os
import sys
from datetime import datetime, timezone

import requests

URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
ZIEL = sys.argv[1] if len(sys.argv) > 1 else "data/btc/btc_tag.json"


def laden():
    """Alle Tageswerte seit 2010, seitenweise."""
    params = {
        "assets": "btc",
        "metrics": "PriceUSD,CapMrktCurUSD,CapMVRVCur",
        "frequency": "1d",
        "start_time": "2010-07-18",
        "page_size": 10000,
    }
    zeilen, url = [], URL
    while url:
        r = requests.get(url, params=params if url == URL else None, timeout=60)
        r.raise_for_status()
        j = r.json()
        zeilen += j.get("data", [])
        url = j.get("next_page_url")
    return zeilen


def zahl(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def main():
    zeilen = laden()
    t, p, mvrv, z = [], [], [], []
    n, summe, quadrate = 0, 0.0, 0.0  # laufende Standardabweichung der Marktkap.
    for r in zeilen:
        preis = zahl(r.get("PriceUSD"))
        if preis is None:
            continue
        mc, m = zahl(r.get("CapMrktCurUSD")), zahl(r.get("CapMVRVCur"))
        tag = int(datetime.fromisoformat(r["time"].replace("Z", "+00:00")).timestamp())
        zwert = None
        if mc is not None:
            n += 1
            summe += mc
            quadrate += mc * mc
            if m and n > 30:
                mittel = summe / n
                sd = math.sqrt(max(quadrate / n - mittel * mittel, 0))
                if sd > 0:
                    zwert = round((mc - mc / m) / sd, 3)
        t.append(tag)
        p.append(round(preis, 2))
        mvrv.append(round(m, 4) if m else None)
        z.append(zwert)
    if len(t) < 3000:
        raise SystemExit(f"Zu wenige Tage ({len(t)}), Datei bleibt unverändert")
    os.makedirs(os.path.dirname(ZIEL), exist_ok=True)
    daten = {
        "stand": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "quelle": "Coin Metrics Community API (PriceUSD, CapMrktCurUSD, CapMVRVCur)",
        "t": t, "p": p, "z": z, "mvrv": mvrv,
    }
    with open(ZIEL, "w", encoding="utf-8") as f:
        json.dump(daten, f, separators=(",", ":"))
    letzte = next(i for i in range(len(z) - 1, -1, -1) if z[i] is not None)
    print(f"{len(t)} Tage, {datetime.utcfromtimestamp(t[0]):%d.%m.%Y} bis {datetime.utcfromtimestamp(t[-1]):%d.%m.%Y}, "
          f"letzter Kurs {p[-1]:,.0f} $, Z-Score {z[letzte]} am {datetime.utcfromtimestamp(t[letzte]):%d.%m.%Y}")


if __name__ == "__main__":
    main()
