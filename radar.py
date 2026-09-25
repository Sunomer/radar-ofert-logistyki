#!/usr/bin/env python3
"""Radar ofert logistyki – zbieracz ofert uruchamiany przez GitHub Actions.

Zbiera oferty z:
  * LinkedIn (publiczne wyniki wyszukiwania bez logowania, ostatnie 24 h) + treść ogłoszeń,
  * SerpAPI (Google z filtrem 24 h + Google Jobs) – tylko gdy RUN_SERP=true i jest SERPAPI_KEY.

Wyniki zapisuje w katalogu data/:
  data/kandydaci.md      – lista kandydatów z ostatnich dni (czyta ją Claude),
  data/oferty/<id>.md    – treść pojedynczego ogłoszenia,
  data/status.json       – stan ostatniego przebiegu,
  data/seen.json         – pamięć już widzianych ofert.

Tylko biblioteka standardowa Pythona, bez instalowania pakietów.
Klucz SERPAPI_KEY jest czytany ze zmiennej środowiskowej (GitHub Secret) i nigdy nie jest zapisywany.
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
OFFERS_DIR = os.path.join(DATA, "oferty")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        return e.code, body
    except Exception as e:  # sieć, timeout
        return 0, str(e)


def strip_tags(s):
    s = re.sub(r"(?i)<br\s*/?>", "\n", s or "")
    s = re.sub(r"(?i)</(p|li|ul|ol|div|h\d)>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def first(pattern, text, flags=re.S):
    m = re.search(pattern, text or "", flags)
    return strip_tags(m.group(1)) if m else ""


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def short_hash(s):
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()[:10]


def id_from_link(link, fallback_prefix):
    link = link or ""
    m = re.search(r"pracuj\.pl/.*oferta,(\d+)", link)
    if m:
        return "pracuj-" + m.group(1)
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?]*-)?(\d{6,})", link)
    if m:
        return "linkedin-" + m.group(1)
    return fallback_prefix + "-" + short_hash(link.split("?")[0].rstrip("/").lower())


# ---------------------------------------------------------------- LinkedIn
def linkedin_search(query, location):
    url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
           + urllib.parse.urlencode({"keywords": query, "location": location,
                                     "f_TPR": "r86400", "start": 0}))
    code, body = http_get(url)
    if code != 200:
        return code, []
    items = []
    for block in body.split("<li>")[1:]:
        jid = first(r"urn:li:jobPosting:(\d+)", block)
        if not jid:
            continue
        link = first(r'class="base-card__full-link[^"]*"[^>]*href="([^"]+)"', block)
        link = html.unescape(link).split("?")[0] if link else f"https://pl.linkedin.com/jobs/view/{jid}"
        items.append({
            "id": "linkedin-" + jid,
            "source": "LinkedIn",
            "query": query,
            "title": first(r'<h3[^>]*base-search-card__title[^>]*>(.*?)</h3>', block),
            "company": first(r'<h4[^>]*base-search-card__subtitle[^>]*>(.*?)</h4>', block),
            "location": first(r'<span[^>]*job-search-card__location[^>]*>(.*?)</span>', block),
            "postedAt": first(r'<time[^>]*datetime="([\d-]+)"', block),
            "salary": first(r'<span[^>]*job-search-card__salary-info[^>]*>(.*?)</span>', block),
            "link": link,
        })
    return code, items


def linkedin_details(jid_num):
    code, body = http_get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid_num}")
    if code != 200:
        return code, None
    criteria = []
    for h, v in re.findall(r'description__job-criteria-subheader[^>]*>(.*?)</h3>\s*<span[^>]*>(.*?)</span>', body, re.S):
        criteria.append(f"{strip_tags(h)}: {strip_tags(v)}")
    return code, {
        "title": first(r'<h2[^>]*top-card-layout__title[^>]*>(.*?)</h2>', body),
        "company": first(r'<a[^>]*topcard__org-name-link[^>]*>(.*?)</a>', body),
        "location": first(r'<span[^>]*topcard__flavor--bullet[^>]*>(.*?)</span>', body),
        "postedAgo": first(r'<span[^>]*posted-time-ago__text[^>]*>(.*?)</span>', body),
        "salary": first(r'<div[^>]*compensation__salary[^>]*>(.*?)</div>', body),
        "criteria": criteria,
        "description": first(r'<div[^>]*show-more-less-html__markup[^>]*>(.*?)</div>', body),
    }


# ---------------------------------------------------------------- SerpAPI
def serp_call(params, key):
    p = dict(params, api_key=key, hl="pl", gl="pl")
    code, body = http_get("https://serpapi.com/search.json?" + urllib.parse.urlencode(p), timeout=60)
    try:
        data = json.loads(body)
    except Exception:
        data = {"error": f"HTTP {code}"}
    return data


def run_serp(cfg, key):
    status = {"ran": True, "searches": 0, "results": 0, "note": ""}
    items, errors = [], []
    for q in cfg.get("serpGoogleQueries", []):
        status["searches"] += 1
        d = serp_call({"engine": "google", "q": q, "tbs": "qdr:d", "num": 20,
                       "location": cfg["serpLocation"]}, key)
        if d.get("error"):
            errors.append("Google: " + str(d["error"]))
            continue
        for o in d.get("organic_results") or []:
            if not o.get("link"):
                continue
            items.append({
                "id": id_from_link(o["link"], "google"),
                "source": "Google (SerpAPI)", "query": q,
                "title": o.get("title", ""), "company": "", "location": "",
                "postedAt": o.get("date", ""), "salary": "", "link": o["link"],
                "snippet": o.get("snippet", ""),
            })
    jq = cfg.get("serpJobsQuery")
    if jq:
        status["searches"] += 1
        d = serp_call({"engine": "google_jobs", "q": jq, "location": cfg["serpLocation"]}, key)
        if d.get("error"):
            errors.append("Google Jobs: " + str(d["error"]))
        for j in d.get("jobs_results") or []:
            ext = j.get("detected_extensions") or {}
            posted = str(ext.get("posted_at", ""))
            if posted and not re.search(r"minut|godz|hour|min ago|1 dzie|1 day|dzisiaj|today", posted):
                continue
            opts = j.get("apply_options") or []
            link = (opts[0].get("link") if opts else "") or j.get("share_link", "")
            via = j.get("via", "")
            it = {
                "id": id_from_link(link, "gjobs"),
                "source": "Google Jobs" + (f" ({via})" if via else ""), "query": jq,
                "title": j.get("title", ""), "company": j.get("company_name", ""),
                "location": j.get("location", ""), "postedAt": posted,
                "salary": str(ext.get("salary", "")), "link": link,
                "description": j.get("description", ""),
            }
            items.append(it)
    status["results"] = len(items)
    status["note"] = " | ".join(errors)
    return status, items


# ---------------------------------------------------------------- zapis
def write_offer_file(it, details=None):
    os.makedirs(OFFERS_DIR, exist_ok=True)
    d = details or {}
    lines = [f"# {d.get('title') or it.get('title','')}", "",
             f"- id: {it['id']}",
             f"- firma: {d.get('company') or it.get('company','')}",
             f"- lokalizacja: {d.get('location') or it.get('location','')}",
             f"- źródło: {it.get('source','')}",
             f"- link: {it.get('link','')}",
             f"- opublikowano: {it.get('postedAt','')} {('(' + d['postedAgo'] + ')') if d.get('postedAgo') else ''}".rstrip(),
             f"- wynagrodzenie: {d.get('salary') or it.get('salary') or 'brak w ogłoszeniu'}",
             f"- pobrano: {NOW.isoformat()}"]
    for c in d.get("criteria", []):
        lines.append(f"- {c}")
    desc = d.get("description") or it.get("description") or it.get("snippet") or ""
    lines += ["", "## Treść ogłoszenia", "", desc[:12000] or "(brak treści)"]
    with open(os.path.join(OFFERS_DIR, it["id"] + ".md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    cfg = load_json(os.path.join(ROOT, "config.json"), {})
    os.makedirs(OFFERS_DIR, exist_ok=True)
    seen_path = os.path.join(DATA, "seen.json")
    seen = load_json(seen_path, {})
    inc = re.compile(cfg.get("includeTitle", "."), re.I)
    exc = re.compile(cfg.get("excludeTitle", "(?!x)x"), re.I)

    status = {"runAt": NOW.isoformat(), "linkedin": {}, "serp": {"ran": False}}

    # --- LinkedIn
    li = {"found": 0, "matched": 0, "new": 0, "details": 0, "note": ""}
    collected = {}
    for q in cfg.get("linkedinQueries", []):
        code, items = linkedin_search(q, cfg.get("location", "Warszawa"))
        if code != 200:
            li["note"] = f"LinkedIn zwrócił kod {code} dla frazy '{q}' – przerwano wyszukiwanie LinkedIn."
            log(li["note"])
            break
        li["found"] += len(items)
        for it in items:
            t = it["title"]
            if exc.search(t) or not inc.search(t):
                continue
            collected.setdefault(it["id"], it)
        time.sleep(2)
    li["matched"] = len(collected)

    details_left = int(cfg.get("maxDetailsPerRun", 30))
    for oid, it in collected.items():
        if oid in seen:
            seen[oid]["lastSeen"] = NOW.isoformat()
            continue
        li["new"] += 1
        det = None
        if details_left > 0:
            code, det = linkedin_details(oid.split("-", 1)[1])
            details_left -= 1
            if det:
                li["details"] += 1
                write_offer_file(it, det)
            elif code in (429, 999):
                li["note"] = (li["note"] + f" Treść ogłoszeń: LinkedIn zwrócił {code} (limit).").strip()
                details_left = 0
            time.sleep(2)
        it["hasDetails"] = bool(det)
        it["firstSeen"] = NOW.isoformat()
        it["lastSeen"] = NOW.isoformat()
        seen[oid] = it
    status["linkedin"] = li

    # --- SerpAPI
    key = os.environ.get("SERPAPI_KEY", "").strip()
    if os.environ.get("RUN_SERP", "").lower() == "true":
        if not key:
            status["serp"] = {"ran": False, "note": "Brak sekretu SERPAPI_KEY w repozytorium."}
        else:
            sst, sitems = run_serp(cfg, key)
            new = 0
            for it in sitems:
                if exc.search(it["title"] or ""):
                    continue
                if it["id"] in seen:
                    seen[it["id"]]["lastSeen"] = NOW.isoformat()
                    continue
                new += 1
                it["hasDetails"] = bool(it.get("description"))
                if it["hasDetails"]:
                    write_offer_file(it)
                it.pop("description", None)
                it["firstSeen"] = it["lastSeen"] = NOW.isoformat()
                seen[it["id"]] = it
            sst["new"] = new
            status["serp"] = sst
    else:
        status["serp"] = {"ran": False, "note": "SerpAPI uruchamiane tylko w porannym przebiegu."}

    # --- porządki: usuń stare wpisy i pliki
    keep_after = NOW - dt.timedelta(days=int(cfg.get("keepDays", 14)))
    for oid in list(seen):
        try:
            ls = dt.datetime.fromisoformat(seen[oid].get("lastSeen") or seen[oid].get("firstSeen"))
        except Exception:
            ls = NOW
        if ls < keep_after:
            del seen[oid]
    for fn in os.listdir(OFFERS_DIR):
        if fn.endswith(".md") and fn[:-3] not in seen:
            os.remove(os.path.join(OFFERS_DIR, fn))
    save_json(seen_path, seen)
    save_json(os.path.join(DATA, "status.json"), status)

    # --- lista dla Claude'a
    list_after = NOW - dt.timedelta(days=int(cfg.get("listDays", 3)))
    recent = [v for v in seen.values()
              if dt.datetime.fromisoformat(v["firstSeen"]) >= list_after]
    recent.sort(key=lambda v: v["firstSeen"], reverse=True)
    s = status["serp"]
    out = [
        "# Radar ofert logistyki – kandydaci",
        "",
        f"Ostatni przebieg: {status['runAt']} (UTC)",
        f"LinkedIn: znaleziono {li['found']}, pasujących tytułów {li['matched']}, nowych {li['new']}, pobranych treści {li['details']}. {li['note']}".rstrip(),
        ("SerpAPI: wyszukiwań {searches}, wyników {results}, nowych {new}. {note}".format(
            searches=s.get("searches", 0), results=s.get("results", 0), new=s.get("new", 0), note=s.get("note", ""))
         if s.get("ran") else f"SerpAPI: nie uruchomiono. {s.get('note','')}").rstrip(),
        f"Kandydaci z ostatnich {cfg.get('listDays', 3)} dni: {len(recent)}",
        "Treść ogłoszenia (gdy jest): plik oferty/<id>.md w tym samym katalogu.",
        "",
    ]
    for v in recent:
        out.append("- {id} | {title} | {company} | {loc} | opubl. {posted} | wynagr.: {sal} | {src} | pierwszy raz: {fs} | treść: {det} | {link}".format(
            id=v["id"], title=v.get("title", ""), company=v.get("company", ""), loc=v.get("location", ""),
            posted=v.get("postedAt", "") or "?", sal=v.get("salary", "") or "-", src=v.get("source", ""),
            fs=v["firstSeen"][:16], det="tak" if v.get("hasDetails") else "nie", link=v.get("link", "")))
    with open(os.path.join(DATA, "kandydaci.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    log("\n".join(out[:6]))


if __name__ == "__main__":
    main()
