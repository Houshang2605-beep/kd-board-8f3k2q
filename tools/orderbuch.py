# Datei: orderbuch.py | Ort: tools/orderbuch.py (Repo) oder ~/trading/orderbuch.py (Mac) | neu
"""
Kraken-Orderbuch: mehrere Schnappschüsse, Liquiditäts-Cluster, Spread, Markttiefe.

Was es tut
  - holt das öffentliche Orderbuch von Kraken (kein API-Schlüssel nötig),
    bis zu 500 Stufen je Seite
  - fasst die Orders zu Preisstufen zusammen (Standard: 0,1 % vom Mittelkurs)
  - zeigt je Schnappschuss: Spread, Markttiefe bis 0,5 / 1 / 2 % Abstand,
    Kauf-/Verkaufs-Übergewicht und die 5 größten Cluster je Seite
  - vergleicht am Ende alle Schnappschüsse: Welche Cluster blieben stehen
    (stabil), welche tauchten nur kurz auf (möglicher Spoof)?
  - speichert alles als CSV in ~/trading/orderbuch/

Grenzen (immer mitdenken)
  - nur Kraken; andere Börsen haben eigene Orderbücher
  - sichtbare Limit-Orders; Iceberg-Orders und Dark Pools sieht man nicht
  - 500 Stufen reichen bei BTC oft nur wenige Prozent weit
  - ein Cluster ist ein Hinweis, kein Signal

Aufruf (im Terminal, im Ordner ~/trading)
  source .venv/bin/activate
  python orderbuch.py                       # BTC/EUR, 10 Schnappschüsse, alle 60 s
  python orderbuch.py --paar ETHEUR         # anderes Paar
  python orderbuch.py --anzahl 20 --intervall 30 --stufe 0.25
"""
import argparse
import csv
import json
import os
import time
from datetime import datetime

import requests

URL = "https://api.kraken.com/0/public/Depth"
TIEFEN = (0.5, 1.0, 2.0)  # Abstand vom Mittelkurs in %


def hole_orderbuch(paar):
    """Ein Schnappschuss: (bids, asks) als Listen von (Preis, Menge), beste zuerst."""
    r = requests.get(URL, params={"pair": paar, "count": 500}, timeout=15)
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError("Kraken meldet: " + ", ".join(j["error"]))
    buch = next(iter(j["result"].values()))
    bids = [(float(p), float(v)) for p, v, _ in buch["bids"]]
    asks = [(float(p), float(v)) for p, v, _ in buch["asks"]]
    return bids, asks


def stufe_von(preis, breite):
    """Rundet einen Preis auf seine Preisstufe (Untergrenze der Stufe)."""
    return round((preis // breite) * breite, 8)


def cluster(orders, breite):
    """Fasst Orders je Preisstufe zusammen: {Stufe: Menge}."""
    out = {}
    for p, v in orders:
        s = stufe_von(p, breite)
        out[s] = out.get(s, 0.0) + v
    return out


def auswerten(bids, asks, stufe_prozent):
    """Alle Kennzahlen eines Schnappschusses."""
    bester_bid, bester_ask = bids[0][0], asks[0][0]
    mitte = (bester_bid + bester_ask) / 2
    breite = mitte * stufe_prozent / 100
    tiefe = {}
    for t in TIEFEN:
        unten, oben = mitte * (1 - t / 100), mitte * (1 + t / 100)
        b = sum(p * v for p, v in bids if p >= unten)
        a = sum(p * v for p, v in asks if p <= oben)
        tiefe[t] = (b, a)
    reichweite = (bids[-1][0] / mitte - 1) * 100, (asks[-1][0] / mitte - 1) * 100
    cb, ca = cluster(bids, breite), cluster(asks, breite)
    top_b = sorted(cb.items(), key=lambda x: -x[1])[:5]
    top_a = sorted(ca.items(), key=lambda x: -x[1])[:5]
    return {
        "mitte": mitte,
        "spread": bester_ask - bester_bid,
        "spread_pct": (bester_ask - bester_bid) / mitte * 100,
        "tiefe": tiefe,
        "reichweite": reichweite,
        "breite": breite,
        "top_b": top_b,
        "top_a": top_a,
        "alle_b": cb,
        "alle_a": ca,
    }


def zahl(x, d=2):
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def zeige(nr, zeit, k, basis):
    m = k["mitte"]
    print(f"\n=== Schnappschuss {nr} · {zeit:%d.%m.%Y %H:%M:%S} · Mitte {zahl(m)} ===")
    print(f"Spread {zahl(k['spread'])} ({zahl(k['spread_pct'], 4)} %) · "
          f"Buch reicht {zahl(k['reichweite'][0], 2)} % / +{zahl(k['reichweite'][1], 2)} %")
    for t, (b, a) in k["tiefe"].items():
        quote = b / a if a else float("inf")
        print(f"  Tiefe ±{zahl(t, 1)} %: Kauf {zahl(b, 0)} · Verkauf {zahl(a, 0)} · Verhältnis {zahl(quote)}")
    print(f"  Größte Kauf-Cluster (Stufe {zahl(k['breite'])}):")
    for s, v in k["top_b"]:
        print(f"    {zahl(s):>14}  {zahl(v, 4):>12} {basis}  ≈ {zahl(s * v, 0):>10}  ({zahl((s / m - 1) * 100)} %)")
    print("  Größte Verkaufs-Cluster:")
    for s, v in k["top_a"]:
        print(f"    {zahl(s):>14}  {zahl(v, 4):>12} {basis}  ≈ {zahl(s * v, 0):>10}  (+{zahl((s / m - 1) * 100)} %)")


def stabilitaet(verlauf):
    """Vergleicht alle Schnappschüsse.
    stabil   = in mindestens 70 % der Schnappschüsse unter den Top 5
    möglicher Spoof = nur kurz unter den Top 5 und jetzt weniger als halb so groß wie in der Spitze
    Einmalige, unauffällige Stufen werden nicht aufgeführt."""
    n = len(verlauf)
    zaehler, spitze = {}, {}
    for k in verlauf:
        for seite, top in (("Kauf", k["top_b"]), ("Verkauf", k["top_a"])):
            for s, v in top:
                zaehler[(seite, s)] = zaehler.get((seite, s), 0) + 1
                spitze[(seite, s)] = max(spitze.get((seite, s), 0.0), v)
    letzte = verlauf[-1]
    print(f"\n=== Auswertung über {n} Schnappschüsse ===")
    for seite in ("Kauf", "Verkauf"):
        alle = letzte["alle_b"] if seite == "Kauf" else letzte["alle_a"]
        zeilen = []
        for (sd, s), c in zaehler.items():
            if sd != seite:
                continue
            jetzt, hoch = alle.get(s, 0.0), spitze[(sd, s)]
            if c >= 0.7 * n:
                urteil = "stabil"
            elif c <= max(1, n // 3) and jetzt < 0.5 * hoch:
                urteil = "nur kurz da, möglicher Spoof"
            elif c >= 2:
                urteil = "wechselnd"
            else:
                continue
            zeilen.append((s, c, jetzt, hoch, urteil))
        zeilen.sort(key=lambda x: -x[0] if seite == "Kauf" else x[0])
        print(f"{seite}-Seite:" + ("" if zeilen else " nichts Auffälliges"))
        for s, c, jetzt, hoch, urteil in zeilen:
            print(f"  {zahl(s):>14}  in {c} von {n} Top-5 · Spitze {zahl(hoch, 4)} · jetzt {zahl(jetzt, 4)} · {urteil}")


def json_speichern(ordner, paar, verlauf):
    """Letzter Stand als JSON (für das Dashboard): Kennzahlen, Top-Cluster, Stabilität."""
    n = len(verlauf)
    zaehler = {}
    for k in verlauf:
        for seite, top in (("kauf", k["top_b"]), ("verkauf", k["top_a"])):
            for s, _ in top:
                zaehler[(seite, s)] = zaehler.get((seite, s), 0) + 1
    k = verlauf[-1]
    daten = {
        "paar": paar,
        "zeit": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schnappschuesse": n,
        "mitte": k["mitte"],
        "spread": k["spread"],
        "spread_pct": k["spread_pct"],
        "reichweite_pct": list(k["reichweite"]),
        "stufe": k["breite"],
        "tiefe": {str(t): {"kauf": b, "verkauf": a} for t, (b, a) in k["tiefe"].items()},
        "cluster": {
            seite: [{"stufe": s, "menge": v, "wert": s * v, "abstand_pct": (s / k["mitte"] - 1) * 100,
                     "in_top5": zaehler.get((seite, s), 0)} for s, v in top]
            for seite, top in (("kauf", k["top_b"]), ("verkauf", k["top_a"]))
        },
    }
    with open(os.path.join(ordner, f"{paar}_aktuell.json"), "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)
    # eine Zeile je Lauf im Verlauf (für Vergleiche über Tage)
    verlauf_datei = os.path.join(ordner, f"{paar}_verlauf.csv")
    neu = not os.path.exists(verlauf_datei)
    kb = daten["cluster"]["kauf"][0] if daten["cluster"]["kauf"] else {"stufe": "", "wert": ""}
    kv = daten["cluster"]["verkauf"][0] if daten["cluster"]["verkauf"] else {"stufe": "", "wert": ""}
    with open(verlauf_datei, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        if neu:
            w.writerow(["zeit_utc", "mitte", "spread_pct", "tiefe1_kauf", "tiefe1_verkauf",
                        "groesster_kauf_cluster", "wert_kauf", "groesster_verkauf_cluster", "wert_verkauf"])
        t1 = daten["tiefe"]["1.0"]
        w.writerow([daten["zeit"], f"{daten['mitte']:.8f}", f"{daten['spread_pct']:.5f}",
                    f"{t1['kauf']:.0f}", f"{t1['verkauf']:.0f}", kb["stufe"], f"{kb['wert']:.0f}" if kb["wert"] != "" else "",
                    kv["stufe"], f"{kv['wert']:.0f}" if kv["wert"] != "" else ""])


def main():
    a = argparse.ArgumentParser(description="Kraken-Orderbuch mit Clustern und Schnappschüssen")
    a.add_argument("--paar", default="XBTEUR", help="Kraken-Paar, z. B. XBTEUR, ETHEUR, SOLEUR")
    a.add_argument("--anzahl", type=int, default=10, help="Anzahl Schnappschüsse")
    a.add_argument("--intervall", type=int, default=60, help="Sekunden zwischen Schnappschüssen")
    a.add_argument("--stufe", type=float, default=0.1, help="Breite einer Preisstufe in %% vom Mittelkurs")
    a.add_argument("--ohne-csv", action="store_true", help="keine Einzel-CSV schreiben (nur JSON und Verlauf)")
    a.add_argument("--ordner", default="~/trading/orderbuch", help="Zielordner für CSV und JSON")
    args = a.parse_args()

    basis = args.paar.replace("EUR", "").replace("USD", "") or args.paar
    ordner = os.path.expanduser(args.ordner)
    os.makedirs(ordner, exist_ok=True)
    datei = os.path.join(ordner, f"orderbuch_{args.paar}_{datetime.now():%Y-%m-%d_%H%M}.csv")

    verlauf = []
    with open(os.devnull if args.ohne_csv else datei, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["zeit", "seite", "stufe", "menge", "wert", "abstand_pct", "mitte", "spread"])
        for nr in range(1, args.anzahl + 1):
            zeit = datetime.now()
            try:
                bids, asks = hole_orderbuch(args.paar)
            except Exception as e:
                print(f"\nSchnappschuss {nr} fehlgeschlagen: {e}")
            else:
                k = auswerten(bids, asks, args.stufe)
                verlauf.append(k)
                zeige(nr, zeit, k, basis)
                for seite, alle in (("kauf", k["alle_b"]), ("verkauf", k["alle_a"])):
                    for s, v in alle.items():
                        w.writerow([zeit.isoformat(timespec="seconds"), seite, f"{s:.8f}", f"{v:.8f}",
                                    f"{s * v:.2f}", f"{(s / k['mitte'] - 1) * 100:.4f}",
                                    f"{k['mitte']:.8f}", f"{k['spread']:.8f}"])
                f.flush()
            if nr < args.anzahl:
                time.sleep(args.intervall)

    if len(verlauf) >= 2:
        stabilitaet(verlauf)
    if verlauf:
        json_speichern(ordner, args.paar, verlauf)
    print(f"\nGespeichert in: {ordner}")
    print("Hinweis: nur Kraken, nur sichtbare Orders. Ein Cluster ist ein Hinweis, kein Signal.")


if __name__ == "__main__":
    main()
