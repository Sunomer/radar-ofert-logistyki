"""Radar ofert logistyki: zbiera kandydatów z LinkedIn (strony publiczne) i SerpAPI
(Google z filtrem 24 h + Google Jobs) i zapisuje:
  data/kandydaci.md        – lista kandydatów z dzisiejszego przebiegu
  data/oferty/<id>.md      – treść ogłoszenia (jeśli udało się ją pobrać)
  data/seen.json           – kiedy dany kandydat pojawił się pierwszy raz
Skrypt nigdy nie kończy się błędem – problemy trafiają do nagłówka kandydaci.md.
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
from bs4 import BeautifulSoup

# ---------- USTAWIENIA (możesz zmieniać) ----------
QUERIES = [
    "optymalizacja procesów logistycznych",
    "ciągłe doskonalenie logistyka",
    "lean magazyn",
    "kaizen koordynator",
    "inżynier procesów logistycznych",
    "analityk logistyki",
    "planista logistyki",
    "continuous improvement logistics",
    "warehouse process specialist",
]
LI_LOCATION = "Warszawa, Mazowieckie, Polska"
LI_DISTANCE_MILES = "25"  # ok. 40 km
# SerpAPI ma miesięczny limit wyszukiwań – dlatego tylko kilka zapytań dziennie.
SERP_JOBS_QUERIES = [
    "continuous improvement logistics",
    "logistics process specialist",
    "logistics analyst planner",
]
SERP_GOOGLE_QUERIES = [
    '(lean OR kaizen OR "ciągłego doskonalenia" OR "optymalizacji procesów") logistyka Warszawa '
    '(site:praca.pl OR site:erecruiter.pl OR site:nofluffjobs.com OR site:theprotocol.it OR site:smartrecruiters.com)',
    '("analityk logistyki" OR "planista logistyki" OR "specjalista ds. logistyki" OR "continuous improvement") Warszawa '
    '(site:praca.pl OR site:erecruiter.pl OR site:smartrecruiters.com OR site:teamtailor.com OR site:traffit.com)',
]
SERP_LOCATION = "Warsaw, Masovian Voivodeship, Poland"
# Google Jobs nie obsługuje Polski (gl=pl → błąd 400, sama lokalizacja Warszawa → 503).
# Dlatego pytamy z obsługiwanego kraju, a miasto dopisujemy do zapytania.
# Skrypt sam sprawdza warianty po kolei i zapamiętuje działający w data/serp_state.json.
JOBS_VARIANTS = [
    {"name": "us-en", "suffix": " Warsaw Poland", "params": {"gl": "us", "hl": "en", "google_domain": "google.com"}},
    {"name": "de-en", "suffix": " Warsaw Poland", "params": {"gl": "de", "hl": "en", "google_domain": "google.de"}},
    {"name": "us-pl", "suffix": " Warszawa", "params": {"gl": "us", "hl": "pl", "google_domain": "google.com"}},
]
JOBS_PROBE_QUERY = "logistics specialist"
JOBS_RETRY_DAYS = 14  # gdy żaden wariant nie działa – kolejna próba za 2 tygodnie
NEAR_WARSAW = ("warsz", "warsaw", "varsovie", "warschau", "mazow", "masov", "pruszk", "piaseczn", "ożarów",
               "ozarow", "błoni", "bloni", "janki", "grodzisk", "legionow", "otwock", "marki", "ząbki", "zabki",
               "kobyłk", "kobylk", "zielonk", "nadarzyn", "raszyn", "łomiank", "lomiank", "józefów", "konstancin",
               "wołomin", "wolomin", "sulejówek", "michałowic", "lesznowol", "stare babice", "remote", "zdaln",
               "anywhere", "poland", "polska")
# Wyniki Google, które są listami ofert, słownikami lub artykułami – nie pojedynczym ogłoszeniem
SKIP_LINKS = re.compile(
    r"(jobsora\.|jooble\.|glassdoor\.|adzuna\.|careerjet\.|indeed\.[a-z.]+/(q-|praca\?|jobs\?)|"
    r"pracuj\.pl/praca/[^,]*;kw|praca\.pl/s-|linkedin\.com/jobs/(?!view)|sjp\.pwn|wikipedia\.|"
    r"/blog/|/artykul|/poradnik|youtube\.|facebook\.)", re.I)
MAX_DETAIL_FETCH = 40  # ile treści ogłoszeń pobrać w jednym przebiegu
KEEP_DAYS = 45         # jak długo pamiętać kandydatów i pliki ofert
# ---------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OFFERS_DIR = DATA / "oferty"
SEEN_FILE = DATA / "seen.json"
TZ = ZoneInfo("Europe/Warsaw")
NOW = dt.datetime.now(TZ)
TODAY = NOW.date().isoformat()
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "").strip()


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip().replace("|", "/")


def short_hash(s, n=10):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]


def id_from_url(url):
    m = re.search(r"pracuj\.pl/.*oferta,(\d+)", url)
    if m:
        return f"pracuj-{m.group(1)}"
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?]*-)?(\d{6,})", url)
    if m:
        return f"linkedin-{m.group(1)}"
    m = re.search(r"praca\.pl/.*_(\d+)\.html", url)
    if m:
        return f"praca-{m.group(1)}"
    host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url).split("/")[0])
    host = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")[:20]
    return f"{host}-{short_hash(url)}"


def page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "header", "footer", "nav", "svg"]):
        t.decompose()
    main = soup.find("main") or soup.body or soup
    lines = [clean(x) for x in main.get_text("\n").split("\n")]
    return "\n".join(x for x in lines if x)[:20000]


# ---------------- LinkedIn ----------------
def linkedin(cands, status):
    errors, queries = [], 0
    for q in QUERIES:
        for start in (0, 25):
            queries += 1
            try:
                r = requests.get(
                    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                    params={"keywords": q, "location": LI_LOCATION, "distance": LI_DISTANCE_MILES,
                            "f_TPR": "r86400", "start": start},
                    headers=UA, timeout=30)
            except Exception as e:  # noqa: BLE001
                errors.append(f"„{q}”: {type(e).__name__}")
                break
            if r.status_code != 200:
                errors.append(f"„{q}”: HTTP {r.status_code}")
                if r.status_code == 429:
                    status["li_errors"] = errors
                    status["li_queries"] = queries
                    return
                break
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("li")
            if not cards:
                break
            for li in cards:
                el = li.select_one("[data-entity-urn]")
                m = re.search(r"(\d{6,})", el["data-entity-urn"]) if el else None
                if not m:
                    continue
                jid = m.group(1)
                a = li.select_one("a.base-card__full-link") or li.select_one("a")
                t = li.select_one("time")
                cands.append({
                    "id": f"linkedin-{jid}",
                    "title": clean(getattr(li.select_one(".base-search-card__title"), "text", "")),
                    "company": clean(getattr(li.select_one(".base-search-card__subtitle"), "text", "")),
                    "location": clean(getattr(li.select_one(".job-search-card__location"), "text", "")),
                    "date": (t.get("datetime") if t else "") or "",
                    "salary": clean(getattr(li.select_one(".job-search-card__salary-info"), "text", "")),
                    "source": "LinkedIn",
                    "url": (a["href"].split("?")[0] if a and a.get("href") else f"https://www.linkedin.com/jobs/view/{jid}"),
                    "detail": ("linkedin", jid),
                })
            if len(cards) < 25:
                break
            time.sleep(2)
        time.sleep(2)
    status["li_errors"] = errors
    status["li_queries"] = queries


def linkedin_detail(jid):
    r = requests.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}", headers=UA, timeout=30)
    if r.status_code != 200:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    parts = []
    for item in soup.select(".description__job-criteria-item"):
        k = clean(getattr(item.select_one("h3"), "text", ""))
        v = clean(getattr(item.select_one("span"), "text", ""))
        if k and v:
            parts.append(f"- {k}: {v}")
    body = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    if body:
        for br in body.find_all("br"):
            br.replace_with("\n")
        txt = "\n".join(clean(x) for x in body.get_text("\n").split("\n") if clean(x))
        parts.append("")
        parts.append(txt)
    return "\n".join(parts).strip()


# ---------------- SerpAPI ----------------
def serp_call(params, status):
    status["serp_searches"] += 1
    try:
        r = requests.get("https://serpapi.com/search.json",
                         params={**params, "api_key": SERPAPI_KEY}, timeout=90)
        js = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except requests.Timeout:
        try:  # jedno ponowienie po przekroczeniu czasu
            r = requests.get("https://serpapi.com/search.json",
                             params={**params, "api_key": SERPAPI_KEY}, timeout=90)
            js = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        except Exception as e:  # noqa: BLE001
            status["serp_errors"].append(f"{params.get('engine')}: {type(e).__name__}")
            return None
    except Exception as e:  # noqa: BLE001
        status["serp_errors"].append(f"{params.get('engine')}: {type(e).__name__}")
        return None
    if r.status_code != 200 or js.get("error"):
        msg = js.get("error") or f"HTTP {r.status_code}"
        if "hasn't returned any results" in str(msg):
            return {}
        status["serp_errors"].append(f"{params.get('engine')}: HTTP {r.status_code} – {msg}")
        return None
    return js


def add_jobs(cands, jobs):
    n = 0
    for j in jobs:
        if not any(w in (j.get("location") or "").lower() for w in NEAR_WARSAW):
            continue
        opts = j.get("apply_options") or []
        link = (opts[0].get("link") if opts else "") or j.get("share_link", "")
        if not link:
            continue
        ext = j.get("detected_extensions") or {}
        via = clean(j.get("via", "")).replace("przez ", "").replace("via ", "")
        cid = id_from_url(link)
        if not re.match(r"^(pracuj|linkedin|praca)-", cid):
            cid = "gjobs-" + short_hash(j.get("job_id") or link, 12)
        cands.append({
            "id": cid,
            "title": clean(j.get("title")),
            "company": clean(j.get("company_name")),
            "location": clean(j.get("location")),
            "date": clean(ext.get("posted_at", "")),
            "salary": clean(ext.get("salary", "")),
            "source": f"Google Jobs (via {via})" if via else "Google Jobs",
            "url": link,
            "text": (j.get("description") or "").strip(),
        })
        n += 1
    return n


def serpapi(cands, status):
    if not SERPAPI_KEY:
        status["serp_errors"].append("brak klucza SERPAPI_KEY (sekret w repozytorium)")
        return
    state_file = DATA / "serp_state.json"
    try:
        state = json.loads(state_file.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        state = {}

    def fatal():
        return any(k in e for e in status["serp_errors"] for k in ("run out", "Invalid API key", "HTTP 401", "HTTP 429"))

    def jobs_call(q, v):
        js = serp_call({"engine": "google_jobs", "q": q + v["suffix"], **v["params"]}, status)
        return (js or {}).get("jobs_results") or []

    by_name = {v["name"]: v for v in JOBS_VARIANTS}
    variant = by_name.get(state.get("jobs_variant"))
    if state.get("jobs_disabled_until", "") > TODAY:
        status["jobs_info"] = f"wyłączony do {state['jobs_disabled_until']} (brak ofert dla Polski przy ostatniej próbie)"
    else:
        if variant is None:
            # Próba: szerokie angielskie zapytanie w każdym wariancie, aż któryś zwróci oferty
            probe_start = len(status["serp_errors"])
            for v in JOBS_VARIANTS:
                jobs = jobs_call(JOBS_PROBE_QUERY, v)
                if fatal():
                    return
                if jobs:
                    variant = v
                    add_jobs(cands, jobs)
                    status["serp_errors"][probe_start:] = []  # nieudane warianty to tylko próby
                    break
                time.sleep(1)
            if variant is None:
                until = (NOW.date() + dt.timedelta(days=JOBS_RETRY_DAYS)).isoformat()
                state["jobs_disabled_until"] = until
                status["serp_errors"][probe_start:] = []
                status["jobs_info"] = (f"żaden wariant ({', '.join(by_name)}) nie zwrócił ofert – "
                                       f"wyłączony do {until}, żeby nie zużywać limitu")
            else:
                state["jobs_variant"] = variant["name"]
                state.pop("jobs_disabled_until", None)
        if variant is not None:
            found = 0
            for q in SERP_JOBS_QUERIES:
                found += add_jobs(cands, jobs_call(q, variant))
                if fatal():
                    return
                time.sleep(1)
            state["jobs_last_found"] = found
            if found:
                state["jobs_ok_at"] = TODAY
            elif state.get("jobs_ok_at", "") < (NOW.date() - dt.timedelta(days=3)).isoformat():
                state.pop("jobs_variant", None)  # 3 dni bez wyników – jutro ponowna próba wariantów
            status["jobs_info"] = f"wariant {variant['name']}, ofert z okolic Warszawy: {found}"
    try:
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    except Exception:  # noqa: BLE001
        pass
    for q in SERP_GOOGLE_QUERIES:
        js = serp_call({"engine": "google", "q": q, "tbs": "qdr:d", "hl": "pl", "gl": "pl",
                        "google_domain": "google.pl", "num": 20, "location": SERP_LOCATION}, status)
        if js is None:
            if fatal():
                return
            continue
        for o in js.get("organic_results", []) or []:
            link = o.get("link", "")
            if not link or SKIP_LINKS.search(link):
                continue
            cands.append({
                "id": id_from_url(link),
                "title": clean(o.get("title")),
                "company": "",
                "location": "",
                "date": clean(o.get("date", "")),
                "salary": "",
                "source": "Google (SerpAPI)",
                "url": link,
                "detail": ("page", link),
            })


# ---------------- zapis ----------------
def main():
    DATA.mkdir(exist_ok=True)
    OFFERS_DIR.mkdir(exist_ok=True)
    try:
        seen = json.loads(SEEN_FILE.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        seen = {}
    status = {"li_errors": [], "li_queries": 0, "serp_errors": [], "serp_searches": 0}
    cands = []
    for fn in (linkedin, serpapi):
        try:
            fn(cands, status)
        except Exception as e:  # noqa: BLE001
            key = "li_errors" if fn is linkedin else "serp_errors"
            status[key].append(f"nieoczekiwany błąd: {type(e).__name__}: {e}")

    # duplikaty: to samo id albo ta sama para stanowisko + firma
    uniq, keys = [], set()
    for c in cands:
        pair = (c["title"].lower(), c["company"].lower()) if c["company"] else None
        if c["id"] in keys or (pair and pair in keys):
            continue
        keys.add(c["id"])
        if pair:
            keys.add(pair)
        uniq.append(c)

    fetched = 0
    for c in uniq:
        path = OFFERS_DIR / f"{c['id']}.md"
        text = c.get("text", "")
        if not text and path.exists():
            c["has_text"] = True
            continue
        if not text and c.get("detail") and fetched < MAX_DETAIL_FETCH:
            fetched += 1
            kind, ref = c["detail"]
            try:
                if kind == "linkedin":
                    text = linkedin_detail(ref)
                else:
                    r = requests.get(ref, headers=UA, timeout=30)
                    text = page_text(r.text) if r.status_code == 200 else ""
            except Exception:  # noqa: BLE001
                text = ""
            time.sleep(1.5)
        c["has_text"] = len(text) > 300
        if c["has_text"]:
            head = [f"# {c['title']}", "", f"- Firma: {c['company']}", f"- Lokalizacja: {c['location']}",
                    f"- Data: {c['date']}", f"- Wynagrodzenie: {c['salary']}", f"- Źródło: {c['source']}",
                    f"- Link: {c['url']}", f"- Pobrano: {NOW.isoformat(timespec='minutes')}", "",
                    "## Treść ogłoszenia", ""]
            path.write_text("\n".join(head) + text + "\n", "utf-8")

    for c in uniq:
        seen.setdefault(c["id"], TODAY)
    cutoff = (NOW.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    for f in OFFERS_DIR.glob("*.md"):
        if f.stem not in seen:
            f.unlink()
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")

    n_li = sum(1 for c in uniq if c["source"] == "LinkedIn")
    n_serp = len(uniq) - n_li
    li_err = "; ".join(status["li_errors"]) or "brak"
    serp_err = "; ".join(status["serp_errors"]) or "brak"
    out = [
        "# Kandydaci – radar ofert logistyki",
        "",
        f"Ostatni przebieg: {NOW.strftime('%Y-%m-%d %H:%M')} (Europe/Warsaw), "
        f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')} UTC",
        f"LinkedIn: {n_li} kandydatów, {status['li_queries']} zapytań, błędy: {li_err}",
        f"SerpAPI: {n_serp} kandydatów, {status['serp_searches']} wyszukiwań "
        f"(Google Jobs: {status.get('jobs_info', '-')}), błędy: {serp_err}",
        f"Łącznie: {len(uniq)} kandydatów",
        "",
        "Format: id | stanowisko | firma | lokalizacja | data | wynagrodzenie | źródło | pierwszy raz | treść: tak/nie | link",
        "",
    ]
    for c in uniq:
        out.append(" | ".join([
            c["id"], c["title"], c["company"] or "?", c["location"] or "?", c["date"] or "?",
            c["salary"] or "-", c["source"], seen.get(c["id"], TODAY),
            "treść: " + ("tak" if c.get("has_text") else "nie"), c["url"],
        ]))
    (DATA / "kandydaci.md").write_text("\n".join(out) + "\n", "utf-8")
    print("\n".join(out[:8]))


if __name__ == "__main__":
    main()
