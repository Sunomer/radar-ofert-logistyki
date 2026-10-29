# Radar ofert logistyki – zbieranie z LinkedIn i Google

Dwa workflowy GitHub Actions:

**Radar ofert** (`radar.yml`, codziennie ok. 6:05 zimą / 7:05 latem) uruchamia `scripts/radar.py`, który:

- przeszukuje publiczne (bez logowania) wyniki ofert LinkedIn z ostatnich 24 h, Warszawa + ok. 40 km,
- wykonuje 2 wyszukiwania Google z filtrem 24 h przez SerpAPI (Google Jobs jest wyłączony na stałe),
- zapisuje listę do `data/kandydaci.md`, a treść ogłoszeń do `data/oferty/<id>.md`.

Claude czyta te pliki o 8:00 i ocenia oferty.

**Newsy i cyberataki** (`media.yml`, 3 razy dziennie ok. 4:40, 10:40 i 16:40 czasu polskiego, zimą i latem) uruchamia `scripts/media.py`, który
szuka w Google News (ostatnie 24 h) przez SerpAPI i zapisuje kandydatów do `data/media/newsy.md` i `data/media/cyber.md`.
Claude czyta je ok. 5:22, 11:22 i 17:22, więc o 6:00, 12:00 i 18:00 najnowsze newsy i cyberataki są już na stronie.

## Konfiguracja (jednorazowo)

1. **Wgraj pliki** do repozytorium (Add file → Upload files, przeciągnij całą zawartość folderu).
   Jeśli folder `.github` się nie wgra (ukryty folder), utwórz plik ręcznie: Add file → Create new file,
   nazwa `.github/workflows/radar.yml`, wklej zawartość i zatwierdź.
2. **Klucz SerpAPI**: załóż konto na serpapi.com, skopiuj API key, potem w repozytorium:
   Settings → Secrets and variables → Actions → New repository secret, nazwa `SERPAPI_KEY`.
3. **Uprawnienia zapisu**: Settings → Actions → General → Workflow permissions → „Read and write permissions” → Save.
4. **Pierwsze uruchomienie**: zakładka Actions → „Radar ofert logistyki” → Run workflow.
   Po ok. 2–5 min w `data/kandydaci.md` pojawi się nagłówek „Ostatni przebieg: …”.

## Zużycie SerpAPI (darmowy plan: 250 miesięcznie)

- radar ofert: 2 wyszukiwania dziennie,
- newsy i cyberataki: rano 3 (elektronika, gry, cyberataki), w południe 1 i wieczorem 1 (cyberataki),
- razem 7 dziennie, ok. 217 w miesiącu 31-dniowym.

Licznik zużycia w bieżącym miesiącu jest w `data/serp_usage.json` (liczy od wgrania tej wersji).
`scripts/media.py` przestaje szukać, gdy licznik zbliży się do limitu, żeby zostawić zapas dla ofert.
Zapytania zmieniasz na górze `scripts/radar.py` (`SERP_GOOGLE_QUERIES`) i `scripts/media.py` (`QUERIES`).
Plik `config.json` nie jest używany przez skrypty.

## Uwagi

- LinkedIn bywa niechętny zapytaniom z serwerów GitHuba: może odpowiadać kodem 429.
  Skrypt zapisuje to wtedy w nagłówku, a Claude przekazuje w notatce przebiegu.
  Pomaga zmniejszenie liczby fraz w `config.json` → `linkedin.queries`.
- Frazy i odległość wyszukiwania ustawiasz w `config.json`.
- `data/seen.json` pamięta, kiedy oferta pojawiła się pierwszy raz (60 dni).
