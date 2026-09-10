# Kurswakt

Din portfölj, ett ställe — aktier, fonder och ETF:er med kurser som uppdateras automatiskt.

Byggd på samma sätt som [marknadssignaler](https://github.com/merthedman-rgb/marknadssignaler): allt kör gratis på GitHubs egna servrar (GitHub Actions), du behöver inget eget serverutrymme.

## Hur det hänger ihop

```
GitHub Actions (schemaläggare, kör var 15:e min, vardagar)
        |
        v
fetch_data.py  --  hämtar kurser via Yahoo Finance, klassar aktie/fond/ETF
                    + OMX/Nasdaq-100/guld-grafer, växelkurser, nyheter
        |
        v
data.json  --  senaste kurser, tre index-grafer, växelkurser, nyheter
        |
        v
index.html  --  läser data.json + positions.json live och visar allt
```

`positions.json` är dina innehav (ticker/ISIN, antal, GAV) — den filen rör bakgrundsjobbet aldrig, du äger den helt.

## Det du behöver göra (cirka 15 minuter, en gång)

### 1. Skapa ett GitHub-konto (om du inte redan har ett)
Gratis, på github.com.

### 2. Skapa ett nytt repo och ladda upp filerna
- På github.com: "New repository" → döp det t.ex. `kurswakt` → välj **Private** (portföljen är din egen — ingen anledning att göra den offentlig) → skapa.
- Ladda upp alla filer i den här mappen (dra-och-släpp fungerar på GitHubs webbsida — dra in `index.html`, `fetch_data.py`, `positions.json`, `data.json`, `README.md` och hela `.github`-mappen med `update.yml` inuti).

### 3. Aktivera GitHub Pages
- I ditt repo: **Settings → Pages**.
- Under "Build and deployment" → Source: **Deploy from a branch**.
- Branch: **main**, mapp: **/ (root)** → Save.
- Vänta en minut, ladda om sidan — du får en länk i stil med `https://ditt-användarnamn.github.io/kurswakt/`

### 4. Testa körningen manuellt
- Gå till fliken **Actions** i ditt repo.
- Välj workflowen "Uppdatera Kurswakt" i vänstermenyn.
- Tryck **Run workflow** → **Run workflow** igen för att bekräfta.
- Efter någon minut: kolla att körningen blev grön (bock). Då har `data.json` fått färska kurser.

### 5. Luta dig tillbaka
Från och med nu kör GitHub Actions scriptet automatiskt var 15:e minut (vardagar 05-21 UTC — `fetch_data.py` hoppar själv över en aktie om just dess börs är stängd, så det är säkert att låta jobbet gå oftare än nödvändigt). Sidan hämtar själv den senaste datan varje minut du har den öppen, plus varje gång du växlar tillbaka till fliken.

## Hantera innehav (köp/sälj/lägg till/ta bort)

Redigera `positions.json` direkt på GitHub (öppna filen i repot → pennikonen → ändra → committa), eller be Claude göra det åt dig i en chatt. Format:

```json
{
  "AAPL": { "shares": 5, "avgCost": 307.17 },
  "SE0011337195": { "shares": 10, "avgCost": 100 }
}
```

- Nyckeln är tickern (Yahoo Finance-format, t.ex. `VOLV-B.ST` för Volvo B) eller ett fonds ISIN-nummer.
- `shares` = antal, `avgCost` = ditt genomsnittliga inköpspris per aktie/andel, i instrumentets egen valuta.
- Namn, kurs, valuta och om det är aktie/fond/ETF hämtas automatiskt av `fetch_data.py` nästa gång det kör (inom ~15 minuter) — du behöver aldrig skriva in det själv.
- Tar du bort en rad ur filen försvinner innehavet från sidan efter nästa körning.

## Index- och råvarugrafer

Tre paneler högst upp, samma stil (stort pris, dagsförändring, graf, intervallflikar 1 d./1 v./1 m./3 m./I år/1 år):

- **OMX Stockholm 30** — Yahoo `^OMX`, uppdateras när Stockholmsbörsen har öppet.
- **Nasdaq-100** — Yahoo `^NDX`, uppdateras när amerikanska börsen har öppet.
- **Guld (XAU/USD)** — priset högst upp är riktig spotkurs från [goldprice.dev](https://goldprice.dev) (gratis API, ingen nyckel behövs). Själva grafen/intervallflikarna visar istället guldterminer (Yahoo `GC=F`) eftersom goldprice.devs gratisnivå bara ger 30 dagars historik utan intradagsdata — inte tillräckligt för graferna. Termins- och spotpris ligger normalt ~0,5–1,5% ifrån varandra, vilket är helt normalt. Guld hämtas max en gång i timmen (oavsett hur ofta bakgrundsjobbet kör) för att hålla sig inom goldprice.devs gratiskvot (1 000 anrop/månad).

## Justera senare

- **Schema:** cron-raden i `.github/workflows/update.yml` (`*/15 5-21 * * 1-5` betyder "var 15:e minut, 05-21 UTC, måndag-fredag").
- **Börstider per marknad:** `MARKETS`-dictionaryn i `fetch_data.py` (Stockholm, Oslo, Köpenhamn, Helsingfors, Xetra, London — allt annat antas vara amerikansk börs).
- **Guld-intervallet:** `GOLD_MIN_INTERVAL` i `fetch_data.py` (just nu 55 minuter).
- **Fler/färre intervallflikar:** `RANGE_DEFS` (visning, i `index.html`) och `INDEX_HISTORY_RANGES` (hämtning, i `fetch_data.py`) — håll dem i synk om du lägger till eller tar bort ett intervall.
- **Nyhetskälla:** `fetch_news()` i `fetch_data.py`, skrapar Placera.se:s nyhetslista.

## Viktigt att ha i huvudet

- Det här är ett tekniskt hjälpmedel för att hålla koll på din portfölj — inte investeringsrådgivning.
- Yahoo Finance-data är gratis men inte ett officiellt/garanterat API — om Yahoo ändrar något kan scriptet behöva småputsningar. Hör av dig om en körning börjar felas i Actions-fliken.
- Svenska bank-/fondbolagsfonder utan egen börsnotering (t.ex. de flesta fondrobotars egna fonder) saknar ofta en Yahoo-ticker och kan inte hämtas den här vägen, oavsett om du söker på ticker, namn eller ISIN.
- Om guld slutar visa ett pris: goldprice.devs anonyma gräns är delad per IP och kan i teorin krocka med andra GitHub Actions-körningar. Skapa en gratis nyckel på goldprice.dev, lägg den som repo-secret `GOLDPRICE_API_KEY`, och lägg till `-H "Authorization: Bearer <nyckel>"` i `fetch_data.py`s anrop om det börjar strula.
