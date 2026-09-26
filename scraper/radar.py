#!/usr/bin/env python3
"""Radar ofert logistyki – zbieranie kandydatów z LinkedIn i SerpAPI.

Wynik:
  data/kandydaci.md        – nagłówek ze stanem źródeł + jedna linia na kandydata
  data/oferty/<id>.md      – zapisana treść ogłoszenia
  data/seen.json           – kiedy każdy kandydat pojawił się pierwszy raz
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OFFERS = DATA / "oferty"
SEEN_FILE = DATA / "seen.json"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
TZ = ZoneInfo("Europe/Warsaw")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8"}

# Wyniki Google, które zwykle są listami ofert albo artykułami, a nie pojedynczym ogłoszeniem
LISTING_PATTERNS = [
    r"pracuj\.pl/praca/[^,]*;kw", r"pracuj\.pl/praca/?$", r"indeed\.com/q-", r"indeed\.com/jobs\?",
    r"linkedin\.com/jobs/(?!view)", r"glassdoor\.", r"jooble\.", r"adzuna\.", r"praca\.pl/s-",
    r"infopraca\.pl/praca\?", r"olx\.pl/praca/?$", r"zarobki", r"/blog/", r"/artykul",
    r"aplikuj\.pl/praca/", r"jobsora\.", r"/strona-\d+", r"/q-", r"[?&](page|q|keywords?)=",
]


# Adresy, które zwykle prowadzą do pojedynczego ogłoszenia
OFFER_PATTERNS = [
    r"praca\.pl/[^/]+_\d{6,}\.html", r"pracuj\.pl/praca/[^;]+,oferta,\d+", r"rocketjobs\.pl/oferta-pracy/",
    r"justjoin\.it/(job-offer|offers)/", r"nofluffjobs\.com/.*job/", r"theprotocol\.it/szczegoly/praca/",
    r"indeed\.com/(viewjob|rc/clk)", r"linkedin\.com/jobs/view/", r"olx\.pl/oferta/praca/",
    r"aplikuj\.pl/oferta/", r"gowork\.pl/oferta/", r"infopraca\.pl/praca/.+/\d+", r"jooble\.org/desc/",
    r"/(kariera|careers?|jobs?|oferty-pracy|praca)/.+", r"(job|offer|oferta|stanowisko|vacanc)",
]
JOB_WORDS = r"(specjalist|koordynator|analityk|planist|inżynier|kierownik|lider|manager|specialist|engineer|coordinator|analyst|planner|praca|oferta|job)"


def looks_like_offer(link, title):
    low = link.lower()
    if low.endswith((".pdf", ".doc", ".docx")):
        return False
    if any(re.search(p, low) for p in LISTING_PATTERNS):
        return False
    if re.search(r"\d+\s+ofert|ofert pracy –|jobs in|praca:\s", (title or "").lower()):
        return False  # tytuły list wyników, np. „Praca X, Warszawa – 425 ofert”
    return any(re.search(p, low) for p in OFFER_PATTERNS) and bool(re.search(JOB_WORDS, (title or "").lower()))


def now_utc():
    return datetime.now(timezone.utc)


def clean(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def cell(text):
    """Pole w linii kandydata – bez znaków |, nowych linii."""
    return clean(text).replace("|", "/") or "-"


def excluded(title):
    t = (title or "").lower()
    return any(w in t for w in CONFIG.get("exclude_title_words", []))


def safe_id(prefix, raw):
    raw = re.sub(r"[^A-Za-z0-9_.-]", "", str(raw))
    if not raw or len(raw) > 60:
        raw = hashlib.sha1(str(raw).encode()).hexdigest()[:12]
    return f"{prefix}-{raw}"


def save_offer(cid, cand, body):
    body = (body or "").strip()
    if len(body) < 80:
        return False
    lines = [
        f"# {cand['title']}",
        "",
        f"- Firma: {cand.get('company') or '-'}",
        f"- Lokalizacja: {cand.get('location') or '-'}",
        f"- Data publikacji: {cand.get('date') or '-'}",
        f"- Wynagrodzenie: {cand.get('salary') or '-'}",
        f"- Źródło: {cand['source']}",
        f"- Link: {cand['link']}",
    ]
    for k, v in (cand.get("extra") or {}).items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Treść ogłoszenia", "", body[:20000], ""]
    (OFFERS / f"{cid}.md").write_text("\n".join(lines), encoding="utf-8")
    return True


def html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    for br in soup.find_all(["br", "li", "p", "h1", "h2", "h3", "h4", "div"]):
        br.insert_after("\n")
    text = soup.get_text()
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------- LinkedIn
def linkedin(status):
    cfg = CONFIG["linkedin"]
    base = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    found = {}
    errors = []
    requests_done = 0
    blocked = False
    for q in cfg["queries"]:
        if blocked:
            break
        for page in range(cfg.get("pages_per_query", 1)):
            params = {
                "keywords": q,
                "location": cfg["location"],
                "distance": cfg.get("distance_miles", 25),
                "f_TPR": f"r{int(cfg.get('hours', 24)) * 3600}",
                "start": page * 25,
            }
            try:
                r = requests.get(base, params=params, headers=HEADERS, timeout=30)
                requests_done += 1
            except requests.RequestException as e:
                errors.append(f"„{q}”: błąd połączenia ({type(e).__name__})")
                break
            if r.status_code == 429:
                errors.append(f"kod 429 (LinkedIn ograniczył zapytania) przy „{q}” – przerwano wyszukiwanie")
                blocked = True
                break
            if r.status_code != 200:
                errors.append(f"„{q}”: kod {r.status_code}")
                break
            cards = parse_linkedin_cards(r.text)
            for c in cards:
                found.setdefault(c["jid"], c)
            if len(cards) < 25:
                break
            time.sleep(2)
        time.sleep(2)

    cands = []
    details = 0
    attempted = 0
    for jid, c in found.items():
        if excluded(c["title"]):
            continue
        cid = f"linkedin-{jid}"
        cand = {
            "id": cid, "title": c["title"], "company": c["company"], "location": c["location"],
            "date": c["date"], "salary": c["salary"], "source": "LinkedIn",
            "link": f"https://pl.linkedin.com/jobs/view/{jid}", "has_body": False,
        }
        if (OFFERS / f"{cid}.md").exists():
            cand["has_body"] = True
        elif details < cfg.get("max_details", 40):
            details += 1
            attempted += 1
            body, extra, err = linkedin_detail(jid)
            if err:
                errors.append(err)
                if "429" in err:
                    details = 10 ** 6  # przestań pobierać szczegóły w tym przebiegu
            if extra:
                cand["extra"] = extra
            cand["has_body"] = save_offer(cid, cand, body)
            time.sleep(2)
        cands.append(cand)

    status["linkedin"] = {
        "requests": requests_done + attempted,
        "count": len(cands),
        "errors": dedupe(errors),
    }
    return cands


def parse_linkedin_cards(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li"):
        urn_el = li.select_one("[data-entity-urn]")
        link_el = li.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        jid = None
        if urn_el and urn_el.get("data-entity-urn"):
            m = re.search(r"(\d{6,})", urn_el["data-entity-urn"])
            jid = m.group(1) if m else None
        if not jid and link_el:
            m = re.search(r"-(\d{6,})(?:\?|$)", link_el.get("href", ""))
            jid = m.group(1) if m else None
        if not jid:
            continue
        title = li.select_one(".base-search-card__title")
        comp = li.select_one(".base-search-card__subtitle")
        loc = li.select_one(".job-search-card__location")
        t = li.select_one("time")
        sal = li.select_one(".job-search-card__salary-info")
        out.append({
            "jid": jid,
            "title": clean(title.get_text()) if title else "",
            "company": clean(comp.get_text()) if comp else "",
            "location": clean(loc.get_text()) if loc else "",
            "date": (t.get("datetime") if t else "") or "",
            "salary": clean(sal.get_text()) if sal else "",
        })
    return out


def linkedin_detail(jid):
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        return "", None, f"szczegóły {jid}: błąd połączenia ({type(e).__name__})"
    if r.status_code == 429:
        return "", None, "kod 429 (limit zapytań LinkedIn) przy pobieraniu treści ogłoszeń"
    if r.status_code != 200:
        return "", None, f"szczegóły {jid}: kod {r.status_code}"
    soup = BeautifulSoup(r.text, "html.parser")
    desc = soup.select_one(".show-more-less-html__markup, .description__text")
    extra = {}
    for item in soup.select(".description__job-criteria-item"):
        k = item.select_one(".description__job-criteria-subheader")
        v = item.select_one(".description__job-criteria-text")
        if k and v:
            extra[clean(k.get_text())] = clean(v.get_text())
    sal = soup.select_one(".salary, .compensation__salary")
    if sal:
        extra["Wynagrodzenie (LinkedIn)"] = clean(sal.get_text())
    body = html_to_text(str(desc)) if desc else ""
    return body, extra, None


# ---------------------------------------------------------------- SerpAPI
class SerpStop(Exception):
    pass


def serp_call(params, status):
    key = os.environ.get("SERPAPI_KEY", "").strip()
    if not key:
        raise SerpStop("brak klucza SERPAPI_KEY")
    params = dict(params, api_key=key, hl="pl")
    if params.get("engine") == "google":
        params["gl"] = "pl"  # Google Jobs nie obsługuje gl=pl – tam wystarcza location
    label = f"{'Google Jobs' if params.get('engine') == 'google_jobs' else 'Google'} „{params.get('q', '')[:40]}”"
    r = None
    for attempt in (1, 2):
        try:
            r = requests.get("https://serpapi.com/search.json", params=params, timeout=150)
            break
        except requests.RequestException as e:
            if attempt == 2:
                status["serp"]["errors"].append(f"{label}: błąd połączenia ({type(e).__name__}) po 2 próbach")
                return None
            time.sleep(10)
    status["serp"]["searches"] += 1
    try:
        data = r.json()
    except ValueError:
        data = {}
    err = data.get("error", "")
    if r.status_code == 401 or "invalid api key" in err.lower():
        raise SerpStop("błędny klucz SERPAPI_KEY (kod 401)")
    if r.status_code == 429 or "run out of searches" in err.lower() or "limit" in err.lower():
        raise SerpStop(f"wyczerpany limit wyszukiwań SerpAPI (kod {r.status_code}): {err or 'brak szczegółów'}")
    if r.status_code != 200:
        status["serp"]["errors"].append(f"{label}: kod {r.status_code}: {err or 'brak szczegółów'}")
        return None
    if "hasn't returned any results" in err:
        status["serp"].setdefault("empty", []).append(label)
    elif err:
        status["serp"]["errors"].append(f"{label}: {err}")
    return data


def serpapi(status):
    cfg = CONFIG["serpapi"]
    status["serp"] = {"searches": 0, "google": 0, "jobs": 0, "errors": [], "stopped": ""}
    cands = []
    seen_links = set()
    try:
        for q in cfg.get("google_jobs_queries", []):
            data = serp_call({"engine": "google_jobs", "q": q, "location": cfg["location"]}, status)
            for j in (data or {}).get("jobs_results", []):
                c = google_job(j)
                if c and c["link"] not in seen_links and not excluded(c["title"]):
                    seen_links.add(c["link"])
                    cands.append(c)
                    status["serp"]["jobs"] += 1
        fetches = 0
        for q in cfg.get("google_queries", []):
            data = serp_call({"engine": "google", "q": q, "location": cfg["location"],
                              "tbs": "qdr:d", "num": 20}, status)
            for res in (data or {}).get("organic_results", []):
                link = res.get("link", "")
                title = clean(res.get("title", ""))
                if not link or link in seen_links or excluded(title):
                    continue
                if not looks_like_offer(link, title):
                    continue
                seen_links.add(link)
                cid = safe_id("google", hashlib.sha1(link.encode()).hexdigest()[:12])
                cand = {"id": cid, "title": title, "company": "", "location": "",
                        "date": clean(res.get("date", "")), "salary": "",
                        "source": "Google (SerpAPI)", "link": link, "has_body": False}
                if (OFFERS / f"{cid}.md").exists():
                    cand["has_body"] = True
                elif fetches < cfg.get("max_page_fetches", 10):
                    fetches += 1
                    body = fetch_page_text(link)
                    cand["has_body"] = save_offer(cid, cand, body or "")
                cands.append(cand)
                status["serp"]["google"] += 1
    except SerpStop as e:
        status["serp"]["stopped"] = str(e)
    return cands


def google_job(j):
    title = clean(j.get("title"))
    if not title:
        return None
    opts = j.get("apply_options") or []
    link = (opts[0].get("link") if opts else "") or j.get("share_link") or ""
    if not link:
        return None
    via = clean(j.get("via", "")).replace("przez ", "").replace("via ", "")
    ext = j.get("detected_extensions") or {}
    raw_id = j.get("job_id", "") or link
    cid = safe_id("gjobs", hashlib.sha1(raw_id.encode()).hexdigest()[:12])
    cand = {
        "id": cid, "title": title, "company": clean(j.get("company_name")),
        "location": clean(j.get("location")), "date": clean(ext.get("posted_at", "")),
        "salary": clean(ext.get("salary", "")),
        "source": f"Google Jobs (via {via})" if via else "Google Jobs",
        "link": link, "has_body": False,
        "extra": {"Inne linki do aplikowania": ", ".join(o.get("link", "") for o in opts[1:4])} if len(opts) > 1 else None,
    }
    cand["has_body"] = (OFFERS / f"{cid}.md").exists() or save_offer(cid, cand, j.get("description", ""))
    return cand


def fetch_page_text(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            return ""
        return html_to_text(r.text)
    except requests.RequestException:
        return ""


# ---------------------------------------------------------------- zapis
def dedupe(items):
    out = []
    for i in items:
        if i not in out:
            out.append(i)
    return out


def load_seen():
    """Wczytaj seen.json odpornie na inne/stare formaty (wartość: data albo obiekt z datą)."""
    try:
        raw = json.loads(SEEN_FILE.read_text(encoding="utf-8")) if SEEN_FILE.exists() else {}
    except (ValueError, OSError):
        return {}
    items = raw.items() if isinstance(raw, dict) else []
    out = {}
    for k, v in items:
        if isinstance(v, dict):
            v = next((v.get(f) for f in ("first", "first_seen", "firstSeen", "date", "seen")
                      if isinstance(v.get(f), str)), "")
        if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
            out[str(k)] = v[:10]
    return out


def main():
    OFFERS.mkdir(parents=True, exist_ok=True)
    seen = load_seen()
    status = {}
    started = now_utc()

    try:
        li = linkedin(status)
    except Exception as e:  # nie przerywaj całego przebiegu
        li = []
        status["linkedin"] = {"requests": 0, "count": 0, "errors": [f"wyjątek: {type(e).__name__}: {e}"]}
    try:
        sp = serpapi(status)
    except Exception as e:
        sp = []
        status.setdefault("serp", {"searches": 0, "google": 0, "jobs": 0, "errors": [], "stopped": ""})
        status["serp"]["errors"].append(f"wyjątek: {type(e).__name__}: {e}")

    today = started.astimezone(TZ).strftime("%Y-%m-%d")
    for c in li + sp:
        seen.setdefault(c["id"], today)
    # sprzątanie: zapomnij kandydatów starszych niż 60 dni
    cutoff = (started - timedelta(days=60)).astimezone(TZ).strftime("%Y-%m-%d")
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    L = status["linkedin"]
    S = status["serp"]
    li_state = "BŁĄD" if (L["errors"] and L["count"] == 0) else ("OK z uwagami" if L["errors"] else "OK")
    sp_state = "BŁĄD" if S.get("stopped") and not sp else ("OK z uwagami" if (S.get("stopped") or S["errors"]) else "OK")
    sp_errs = ([S["stopped"]] if S.get("stopped") else []) + dedupe(S["errors"])
    if S.get("empty"):
        sp_errs.append("bez wyników: " + ", ".join(S["empty"]))

    local = started.astimezone(TZ)
    out = [
        "# Kandydaci – Radar ofert logistyki",
        "",
        f"Ostatni przebieg: {local.strftime('%Y-%m-%d %H:%M')} (Europe/Warsaw) = {started.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"LinkedIn: {li_state} – kandydatów: {L['count']}, zapytań HTTP: {L['requests']}; błędy: {'; '.join(L['errors'][:5]) or 'brak'}",
        f"SerpAPI: {sp_state} – wyszukiwań SerpAPI: {S['searches']}, kandydatów: {len(sp)} (Google: {S['google']}, Google Jobs: {S['jobs']}); błędy: {'; '.join(sp_errs[:8]) or 'brak'}",
        "",
        "Format: id | stanowisko | firma | lokalizacja | data | wynagrodzenie | źródło | pierwszy raz | treść: tak/nie | link",
        "",
    ]
    for c in li + sp:
        out.append(" | ".join([
            c["id"], cell(c["title"]), cell(c["company"]), cell(c["location"]), cell(c["date"]),
            cell(c["salary"]), cell(c["source"]), seen.get(c["id"], today),
            f"treść: {'tak' if c['has_body'] else 'nie'}", c["link"],
        ]))
    if not (li or sp):
        out.append("(brak kandydatów w tym przebiegu)")
    (DATA / "kandydaci.md").write_text("\n".join(out) + "\n", encoding="utf-8")

    # usuń zapisane treści ofert, o których seen.json już zapomniał (> 60 dni)
    for f in OFFERS.glob("*.md"):
        if f.stem not in seen:
            f.unlink()

    print("\n".join(out[:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
