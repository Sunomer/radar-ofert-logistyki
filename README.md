# Radar ofert logistyki – zbieracz ofert na GitHubie

Ten folder to gotowe repozytorium dla GitHuba. GitHub Actions (darmowe dla publicznych repozytoriów) trzy razy dziennie:

- sprawdza LinkedIn (oferty z ostatnich 24 h, bez logowania) i pobiera treść nowych ogłoszeń,
- raz dziennie (rano) pyta SerpAPI: Google z filtrem 24 h + Google Jobs,
- zapisuje wyniki w katalogu `data/`.

Potem zadanie Claude'a (w chmurze, bez Twojego komputera) czyta `data/kandydaci.md`, dokłada oferty z pracuj.pl, ocenia je według Twojego profilu i zapisuje w radarze.

## Konfiguracja (jednorazowo, ok. 10 minut)

1. Załóż konto na https://github.com (jeśli nie masz).
2. Utwórz nowe repozytorium: **New repository** → nazwa np. `radar-ofert-logistyki` → **Public** → **Create repository**.
   Musi być publiczne: wtedy Actions są bez limitu minut, a Claude może czytać wyniki bez logowania.
   W repozytorium będą tylko wyniki wyszukiwania ofert i frazy z `config.json`, żadnych danych osobowych ani klucza.
3. Wgraj pliki: na stronie repozytorium **Add file → Upload files**, przeciągnij `radar.py`, `config.json`, `README.md`
   i folder `data` → **Commit changes**.
   Potem dodaj harmonogram: **Add file → Create new file**, w polu nazwy wpisz dokładnie
   `.github/workflows/radar.yml` (ukośniki same utworzą foldery), wklej całą treść pliku `radar.yml` z tego folderu
   → **Commit changes**. (Plik `radar.yml` leżący w głównym folderze możesz potem usunąć – działa tylko ten w `.github/workflows`.)
4. Dodaj klucz SerpAPI jako sekret: **Settings → Secrets and variables → Actions → New repository secret**,
   nazwa `SERPAPI_KEY`, wartość – Twój klucz. Klucz nie jest nigdzie zapisywany ani wypisywany.
5. Uruchom pierwszy raz ręcznie: zakładka **Actions** → „Radar ofert logistyki” → **Run workflow**
   (zaznacz „SerpAPI”, jeśli chcesz od razu sprawdzić klucz). Po minucie w repozytorium pojawi się `data/kandydaci.md`.
6. Podaj Claude'owi adres repozytorium (np. `https://github.com/TWOJ-LOGIN/radar-ofert-logistyki`) –
   wpisze go w konfigurację radaru.

## Zmiany

- Frazy, filtry tytułów i limity: `config.json` (edytujesz na GitHubie ikoną ołówka).
- Godziny: `.github/workflows/radar.yml` (czas UTC).
- Limit SerpAPI: 3 wyszukiwania dziennie ≈ 90 miesięcznie, czyli w granicach darmowego planu. Jeśli dodasz frazy
  do `serpGoogleQueries`, każda to +1 wyszukiwanie dziennie.

## Uwagi

- LinkedIn może czasem zwrócić błąd 429 (za dużo zapytań z serwerów GitHuba). Wtedy przebieg to zapisze w `data/status.json`,
  a następny spróbuje ponownie.
- GitHub wyłącza harmonogram w repozytorium bez aktywności przez 60 dni. Tu każdy przebieg z nowymi wynikami robi commit,
  więc nie powinno się to zdarzyć; gdyby jednak, w zakładce **Actions** jest przycisk ponownego włączenia.
- Stary skrypt `serpapi-logistyka.ps1` nadal działa lokalnie, ale nie jest już potrzebny.
