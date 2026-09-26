# Radar ofert logistyki – zbieranie z LinkedIn i Google

Codziennie o ok. 7:07 (czas letni) GitHub Actions uruchamia `scraper/radar.py`, który:

- przeszukuje publiczne (bez logowania) wyniki ofert LinkedIn z ostatnich 24 h, Warszawa + ok. 40 km,
- wykonuje wyszukiwania SerpAPI: Google Jobs i Google z filtrem 24 h,
- zapisuje listę do `data/kandydaci.md`, a treść ogłoszeń do `data/oferty/<id>.md`.

Claude czyta te pliki o 8:00 i ocenia oferty.

## Konfiguracja (jednorazowo)

1. **Wgraj pliki** do repozytorium (Add file → Upload files, przeciągnij całą zawartość folderu).
   Jeśli folder `.github` się nie wgra (ukryty folder), utwórz plik ręcznie: Add file → Create new file,
   nazwa `.github/workflows/radar.yml`, wklej zawartość i zatwierdź.
2. **Klucz SerpAPI**: załóż konto na serpapi.com, skopiuj API key, potem w repozytorium:
   Settings → Secrets and variables → Actions → New repository secret, nazwa `SERPAPI_KEY`.
3. **Uprawnienia zapisu**: Settings → Actions → General → Workflow permissions → „Read and write permissions” → Save.
4. **Pierwsze uruchomienie**: zakładka Actions → „Radar ofert logistyki” → Run workflow.
   Po ok. 2–5 min w `data/kandydaci.md` pojawi się nagłówek „Ostatni przebieg: …”.

## Zużycie SerpAPI

Domyślnie 5 wyszukiwań dziennie (3 Google Jobs + 2 Google), czyli ok. 150 miesięcznie.
Zapytania zmienisz w `config.json` (`google_jobs_queries`, `google_queries`). Pamiętaj o limicie swojego planu.

## Uwagi

- LinkedIn bywa niechętny zapytaniom z serwerów GitHuba: może odpowiadać kodem 429.
  Skrypt zapisuje to wtedy w nagłówku, a Claude przekazuje w notatce przebiegu.
  Pomaga zmniejszenie liczby fraz w `config.json` → `linkedin.queries`.
- Frazy i odległość wyszukiwania ustawiasz w `config.json`.
- `data/seen.json` pamięta, kiedy oferta pojawiła się pierwszy raz (60 dni).
