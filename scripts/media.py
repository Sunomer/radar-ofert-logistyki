"""Newsy i cyberataki przez SerpAPI (Google News) dla zakładek „Newsy” i „Cyberataki”.

Uruchamiany 3 razy dziennie przez .github/workflows/media.yml. Zapisuje:
  data/media/newsy.md   – kandydaci na newsy (elektronika i gry) z ostatnich ~3 dni
  data/media/cyber.md   – kandydaci na cyberataki z ostatnich ~3 dni
  data/media/seen.json  – kiedy dany link pojawił się pierwszy raz
  data/serp_usage.json  – licznik wyszukiwań SerpAPI w miesiącu (wspólny z radar.py)
Claude czyta te pliki przy każdym przebiegu i sam sprawdza artykuły.
Skrypt nigdy nie kończy się błędem – problemy trafiają do nagłówków plików.

BUDŻET: darmowy plan SerpAPI to 250 wyszukiwań miesięcznie.
  radar ofert (radar.py):  2 dziennie
  ten skrypt:              rano 3 (elektronika, gry, cyberataki), w południe 1 i wieczorem 1 (cyberataki)
  razem:                   7 dziennie, ok. 217 w 31-dniowym miesiącu
Jeśli dodajesz zapytania, pilnuj, żeby suma nie przekroczyła ok. 8 dziennie.
"""
import datetime as dt
import hashlib
import json
import os
import re
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ---------- USTAWIENIA (możesz zmieniać) ----------
QUERIES = {
    # słot: lista (plik, zapytanie)
    "rano": [
        ("newsy", "Apple OR Nvidia OR AMD OR Intel"),
        ("newsy", '"GTA 6" OR "Witcher 4" OR "League of Legends" OR "Counter-Strike 2"'),
        ("cyber", 'cyberattack OR ransomware OR "data breach"'),
    ],
    "poludnie": [
        ("cyber", 'cyberattack OR ransomware OR "data breach"'),
    ],
    "wieczor": [
        ("cyber", 'cyberattack OR ransomware OR "data breach"'),
    ],
}
MONTHLY_LIMIT = 250    # limit planu SerpAPI – po przekroczeniu skrypt nie wykonuje wyszukiwań
SAFETY_MARGIN = 10     # zapas zostawiany dla radaru ofert
KEEP_HOURS = 72        # jak długo trzymać pozycje w plikach .md
# ---------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MEDIA = DATA / "media"
TZ = ZoneInfo("Europe/Warsaw")
NOW = dt.datetime.now(TZ)
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "").strip()
TITLES = {"newsy": "Newsy – elektronika i gry", "cyber": "Cyberataki"}


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip().replace("|", "/")


def slot_now():
    forced = os.environ.get("RUN_SLOT", "").strip()
    if forced in QUERIES:
        return forced
    h = NOW.hour
    return "rano" if h < 10 else "poludnie" if h < 16 else "wieczor"


def load_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return default


def usage_this_month():
    u = load_json(DATA / "serp_usage.json", {})
    return u, u.get(NOW.strftime("%Y-%m"), {})


def bump_usage(kind, n):
    u, _ = usage_this_month()
    m = u.setdefault(NOW.strftime("%Y-%m"), {})
    m[kind] = m.get(kind, 0) + int(n or 0)
    m["razem"] = sum(v for k, v in m.items() if k != "razem")
    cutoff = (NOW.date() - dt.timedelta(days=120)).strftime("%Y-%m")
    u = {k: v for k, v in u.items() if k >= cutoff}
    (DATA / "serp_usage.json").write_text(json.dumps(u, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")


def serp_news(q, errors):
    params = {"engine": "google", "tbm": "nws", "q": q, "tbs": "qdr:d", "hl": "en", "gl": "us",
              "num": 30, "api_key": SERPAPI_KEY}
    for attempt in (1, 2):
        try:
            r = requests.get("https://serpapi.com/search.json", params=params, timeout=90)
            js = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                errors.append(f"{type(e).__name__}")
                return None
            time.sleep(5)
            continue
        if r.status_code != 200 or js.get("error"):
            msg = str(js.get("error") or f"HTTP {r.status_code}")
            if "hasn't returned any results" in msg:
                return []
            errors.append(f"HTTP {r.status_code} – {msg}")
            return None
        return js.get("news_results") or []
    return None


def main():
    MEDIA.mkdir(parents=True, exist_ok=True)
    slot = slot_now()
    seen = load_json(MEDIA / "seen.json", {})
    items = {k: load_json(MEDIA / f"{k}.json", []) for k in TITLES}
    status = {k: {"searches": 0, "new": 0, "errors": []} for k in TITLES}

    _, month = usage_this_month()
    used = month.get("razem", 0)
    planned = QUERIES[slot]
    budget_ok = used + len(planned) <= MONTHLY_LIMIT - SAFETY_MARGIN
    done = 0
    if not SERPAPI_KEY:
        for k in TITLES:
            status[k]["errors"].append("brak klucza SERPAPI_KEY (sekret w repozytorium)")
    elif not budget_ok:
        for k in TITLES:
            status[k]["errors"].append(f"pominięto: w tym miesiącu zużyto już {used} z {MONTHLY_LIMIT} wyszukiwań")
    else:
        for kind, q in planned:
            res = serp_news(q, status[kind]["errors"])
            status[kind]["searches"] += 1
            done += 1
            if res is None and any(x in " ".join(status[kind]["errors"]) for x in ("run out", "Invalid API key", "401", "429")):
                break
            known = {i["link"] for i in items[kind]}
            for n in res or []:
                link = n.get("link", "")
                if not link or link in known:
                    continue
                known.add(link)
                seen.setdefault(link, NOW.isoformat(timespec="minutes"))
                items[kind].append({
                    "id": hashlib.sha1(link.encode("utf-8")).hexdigest()[:10],
                    "title": clean(n.get("title")),
                    "source": clean((n.get("source") or {}).get("name") if isinstance(n.get("source"), dict) else n.get("source")),
                    "date": clean(n.get("date", "")),
                    "first": seen[link],
                    "link": link,
                    "snippet": clean(n.get("snippet", ""))[:300],
                })
                status[kind]["new"] += 1
            time.sleep(1)
        bump_usage("newsy_i_cyber", done)

    cutoff = (NOW - dt.timedelta(hours=KEEP_HOURS)).isoformat(timespec="minutes")
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    (MEDIA / "seen.json").write_text(json.dumps(seen, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
    _, month = usage_this_month()
    for kind, title in TITLES.items():
        keep = [i for i in items[kind] if i.get("first", "") >= cutoff]
        keep.sort(key=lambda i: i.get("first", ""), reverse=True)
        (MEDIA / f"{kind}.json").write_text(json.dumps(keep, ensure_ascii=False, indent=1), "utf-8")
        st = status[kind]
        out = [
            f"# Kandydaci – {title}",
            "",
            f"Ostatni przebieg: {NOW.strftime('%Y-%m-%d %H:%M')} (Europe/Warsaw), słot: {slot}",
            f"SerpAPI (Google News, 24 h): {st['searches']} wyszukiwań w tym przebiegu, {st['new']} nowych pozycji, "
            f"błędy: {'; '.join(st['errors']) or 'brak'}",
            f"Zużycie SerpAPI w tym miesiącu: {month.get('razem', 0)} z {MONTHLY_LIMIT}",
            f"Pozycji w pliku (ostatnie {KEEP_HOURS} h): {len(keep)}",
            "",
            "Format: id | tytuł | źródło | data w Google | pierwszy raz (Europe/Warsaw) | link | fragment",
            "",
        ]
        for i in keep:
            out.append(" | ".join([i["id"], i["title"], i["source"] or "?", i["date"] or "?", i["first"], i["link"], i["snippet"]]))
        (MEDIA / f"{kind}.md").write_text("\n".join(out) + "\n", "utf-8")
        print("\n".join(out[:6]))


if __name__ == "__main__":
    main()
