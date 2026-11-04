# 🏠 Imotko - the agent that digs through imot.bg for you

You know the feeling: 47 open tabs, every apartment "with an amazing view" (of the neighbouring building), and prices that are hard to compare. 😅

**Imotko** is an AI broker with "10 years of experience" that does the tedious part. You tell it in plain Bulgarian what you are looking for, and it:

- 🔎 crawls imot.bg (up to ~320 listings at a time),
- 📊 compares the price per sq.m with the neighbourhood average and gives every listing a **score**,
- 📸 **looks at the photos** for you ("What kind of fridge is that? Is there damp on the ceiling?"),
- 🔔 **watches** your search and pings you when something new and good shows up,
- 🗺️ **measures distances** on the map ("how far is it to location?") and counts the big supermarket chains (Lidl, Kaufland, Billa, Fantastico, T Market) in each neighbourhood,
- 🤔 **doubts its own data** and tells you when something is uncertain.

And most importantly: it **does not make up listings**. It only shows what it found, with links.

---

## 💬 How to talk to it

Just write:

> "Търся тристаен в София до 200 000 евро, без последен етаж, задължително асансьор."

> "Кой квартал в Пловдив е най-евтин за двустаен?"

> "Виж снимките на тази обява - какъв е хладилникът? [link]"

> "Следи ми двустайни под наем във Варна до 600 евро и ме уведомявай."

(The agent understands Bulgarian: the examples above are "A three-room apartment in Sofia up to 200,000 euro, not on the top floor, elevator required", "Which neighbourhood in Plovdiv is cheapest for a two-room?", "Look at the photos of this listing - what kind of fridge is it?" and "Watch two-room rentals in Varna up to 600 euro and notify me.")

If you leave out the city or the number of rooms, it will ask. It won't quiz you about anything else.

### What it understands

| You say                                                                                           | It does                                                                                                                      |
| ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| "no first/last floor"                                                                             | cuts them from the results                                                                                                   |
| "elevator / parking required"                                                                     | keeps only listings that have them                                                                                           |
| "it matters to me..." (elevator, parking, brick, new build, Act 16, furnished, terrace, discount) | gives them more weight in the score                                                                                          |
| "brick / panel / EPK only"                                                                        | filters by construction type                                                                                                 |
| "best deals first"                                                                                | sorts by score (otherwise newest first)                                                                                      |
| "watch this" / "notify me"                                                                        | saves the search and later shows only new listings                                                                           |
| "I work at..." / "I often go to..." (up to 4 places)                                              | closer neighbourhoods (by car, to the first place) get more points; car and public transport times are shown for every place |
| "how far is it to..."                                                                             | distance on the map: straight line, walking, car, public transport                                                           |

### 🗺️ The map, and how much to trust it

- Listings almost never have an exact address. Imotko uses **the street** when the listing has one (about 1 in 5 in Sofia), otherwise **the neighbourhood**, and always says which.
- Points for supermarkets and for travel time to work are **the same for every listing in a neighbourhood** and are bonuses only, so a listing without a street never loses points for it.
- Car times are **realistic city times**, not empty-street times: the route and the empty-street time come from OSRM, plus the minutes a city trip really loses at traffic lights and in traffic, measured on real trips (see below). Checked against Google Maps on 8 Sofia routes: about 13% off on average, never more than 25%.
- **Public transport** times come from the official timetables (GTFS) that the municipalities publish, for a weekday morning (the median of departures between 7:40 and 8:40, including waiting at the stop and walking). The route is calculated locally, with no extra service. Checked against Google Maps on 8 Sofia routes: about 11% off on average, never more than 25%. Cities with timetables: Sofia (incl. metro, tram, trolleybus), Plovdiv, Varna, Burgas, Stara Zagora, Pleven, Veliko Tarnovo, Dobrich, Sliven, Kazanlak, Samokov, Aytos (Yambol's data has expired). A city's timetable is downloaded into `data/gtfs/` the first time it is needed, and `watch.py` refreshes it weekly. Sofia data: Urban Mobility Center (sofiatraffic.bg); other cities: the National Access Point (sipbg.gov.bg) and the Mobility Database.
- Data comes from OpenStreetMap (Nominatim, OSRM, Overpass) and is cached in the database: neighbourhoods and streets for six months, supermarkets for a month. `watch.py` refreshes the supermarkets. The first download for a city takes about 2 minutes and runs in the background.
- When something looks wrong (a place outside the city, a price 3 times below the neighbourhood, a listing that contradicts itself), the answer contains ⚠ and the agent tells you.

---

## 🚀 Up and running in 2 minutes

You need [uv](https://docs.astral.sh/uv/), Python 3.14 (uv will download it itself) and a free key from [OpenRouter](https://openrouter.ai/).

```bash
git clone <repository URL>
cd imotko
uv sync
```

Create a `.env` file from the template and put your key in it:

```bash
cp .env.example .env
```

Done. Pick how you want to use it:

**1. In the terminal** - the quickest:

```bash
uv run chat.py
```

**2. As a web server** (Agno's AgentOS) - with an API and a UI:

```bash
uv run app.py
```

**3. As a listing watcher** - checks saved searches and shows only what is new:

```bash
uv run watch.py
```

---

## 🔔 Phone notifications

Don't want to open the terminal every hour? Put this in `.env`:

```bash
NTFY_TOPIC=some-secret-topic-name
```

Install the [ntfy](https://ntfy.sh/) app, subscribe to the same topic, and new listings will arrive as notifications. The topic is public to anyone who knows it, so pick something unique.

To run it automatically, add this to `crontab -e`:

```cron
0 * * * * cd /path/to/imotko && uv run watch.py
```

---

## 🐳 Docker

The easiest way to run it and keep it running: on your own server, NAS or a cheap VPS, without your laptop having to stay open. 24/7.

```bash
echo "OPENROUTER_API_KEY=your_key" > .env
docker compose up -d --build
```

This starts two services:

| Service  | What it does                                                                                                                              |
| -------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `imotko` | the agent (AgentOS) at http://localhost:7777. You chat with it through [os.agno.com](https://os.agno.com) by pointing it at this address. |
| `watch`  | checks saved searches every hour. If `NTFY_TOPIC` is set in `.env`, new listings arrive as notifications.                                 |

Good to know:

- 💾 The databases (`imot.db` and `imot_history.db`) live in the `data/` folder and survive restarts and updates. Don't delete them if you care about saved searches and price history.
- 🔑 `.env` is not baked into the image; it is read at startup.
- 🔄 New version of the code: `git pull && docker compose up -d --build`.
- 📜 Logs: `docker compose logs -f imotko` (or `watch`).
- 🛑 Stop: `docker compose down` (data is kept).

If you run it on a server on the internet, don't expose port 7777 to the world without protection (a reverse proxy with a password, or a VPN).

---

## ✅ Checking that the answers are right

Three layers, from the data up to the model:

1. **Tools vs. known facts.** `imotko/quality/ground_truth.json` holds facts checked by a person: real trips with real times, neighbourhoods, streets and places. Run the check with:

   ```bash
   uv run python -m imotko.quality.checks
   ```

   Trips with a `real_transit_minutes` value also check the public transport time (±30%). `watch.py` runs it once a week and sends a notification if something no longer matches. Real trips also set how many minutes are added to the OSRM time (currently about 6), so every trip you add makes the times more accurate.

2. **Every number in an answer must come from a tool.** After each answer, a check looks for numbers that no tool returned (allowing for rounding, and for sums and differences next to words like "difference" or "total"). If it finds any, it adds a ⚠ note and saves the answer in the database. In the web UI the answer is streamed, so the note may not show there, but it is always saved.

   Sample questions with the expected behaviour live in `evals/agent_cases.json`. Run them after changing the instructions or the model (this calls the real model and imot.bg):

   ```bash
   uv run python evals/run_agent_evals.py
   ```

3. **Your corrections.** Tell the agent when something is wrong, e.g. "No, it's 20 minutes by car from Elin Vrah to UNWE". It calls `report_error`, saves the correction, and a car-time correction immediately updates the added minutes. `python -m imotko.quality.checks` also lists the latest corrections and flagged answers.

### 💸 Keeping the token bill small

Every tool call sends the whole conversation to the model again, so Imotko tries to make few, small calls:

- `distance_between` and `commute_to` take several places at once (one call instead of one per pair).
- A search returns at most 15 listings, with short map links.
- The same search with the same filters is not run twice in one answer.
- Older tool results are dropped from the chat history (only the last 2 are kept); the model still sees its own earlier answers.
- Photos are shrunk to 1024 px before they reach the model.

## ⚙️ Configuration (everything is optional)

Everything is set in `.env`:

| Variable                     | What it is for                                           | Default                                  |
| ---------------------------- | -------------------------------------------------------- | ---------------------------------------- |
| `OPENROUTER_API_KEY`         | your OpenRouter key (**required**)                       | -                                        |
| `IMOT_MODEL`                 | the main model                                           | `google/gemma-4-31b-it:free`             |
| `IMOT_FALLBACK_MODEL`        | fallback, used when the main one returns 429             | `nvidia/nemotron-3-ultra-550b-a55b:free` |
| `IMOT_VISION_MODEL`          | model for photos (must be multimodal)                    | `google/gemma-4-31b-it:free`             |
| `IMOT_VISION_FALLBACK_MODEL` | fallback for photos                                      | `nvidia/nemotron-3.5-super-vl:free`      |
| `WATCH_MIN_SCORE`            | minimum score a new listing needs in order to be flagged | `20`                                     |
| `NTFY_TOPIC`                 | ntfy.sh topic for notifications                          | disabled                                 |

> 💡 Free models sometimes return 429 ("I'm overloaded"). That is why there is a fallback - don't panic if an answer is slow.

---

## 🗂️ Project layout

```text
app.py, chat.py, watch.py   web server, terminal chat, hourly check of saved searches
imotko/
  agent/        the agent: tools the model can call, instructions, hooks
  search/       scan (download, filters, details), scoring, formatting (text for the model), saved searches
  geo/          geocode (places, streets, addresses), driving (OSRM + calibration), transit (GTFS),
                shops (supermarket chains), enrich (map data for listings), distance (distance_between)
  quality/      monitoring (numbers in answers), checks + ground_truth.json (known facts)
  sources/      listing sites (imot.bg)
  vision.py     photo analysis
  store.py      SQLite: price history, saved searches, map cache, feedback
tests/          unit tests, no network and no API keys needed
evals/          sample questions for the real model
```

Modules import each other as modules (`geocode.locate(...)`, not `from ... import locate`), so a test can patch a function in one place.

## 🧪 Tests

```bash
uv run python -m unittest discover -s tests
```

GitHub Actions (`.github/workflows/tests.yml`) runs them on every push to `main` and on every pull request, and checks that the Docker image builds. The tests need no network and no keys: every call to imot.bg, the map and the models is mocked.

## 🔌 Want another site or another notification channel?

You don't touch the rest of the code, you only subclass:

- new listings site (homes.bg, alo.bg...) -> a class that extends `ListingSource` in `imotko/sources/`, then register it in `imotko/sources/__init__.py`;
- new channel (Telegram, email...) -> a class that extends `Notifier` in `imotko/notify.py`. You only need the `send` method.

Price history and saved searches are stored in `imot_history.db` (in Docker, in `data/`), outside of git.

---

## ⚠️ Honest disclaimer, so you don't get upset later

- Imotko is a **helper, not an oracle**. The score is a guideline, not a verdict. It does not replace viewing the property in person.
- Photo analysis is "by eye". The size of a fridge, for example, is approximate.
- Distances are by street or by neighbourhood, not by exact address. In big neighbourhoods (Mladost, Lyulin) the error can be 1-2 km.
- The data comes from the public pages of imot.bg. Don't send requests more often than needed, and be a good citizen.

Good luck with the search, and may you find a home, not just a "good investment"! 🏡
