# 🏠 Imotko - the agent that digs through imot.bg for you

You know the feeling: 47 open tabs, every apartment "with an amazing view" (of the neighbouring building), and prices that are hard to compare. 😅

**Imotko** is an AI broker with "10 years of experience" that does the tedious part. You tell it in plain Bulgarian what you are looking for, and it:

- 🔎 crawls imot.bg (up to ~320 listings at a time),
- 📊 compares the price per sq.m with the neighbourhood average and gives every listing a **score**,
- 📸 **looks at the photos** for you ("What kind of fridge is that? Is there damp on the ceiling?"),
- 🔔 **watches** your search and pings you when something new and good shows up.

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

| You say                                                                               | It does                                          |
| ------------------------------------------------------------------------------------- | ------------------------------------------------ |
| "no first/last floor"                                                                 | cuts them from the results                       |
| "elevator / parking required"                                                         | keeps only listings that have them               |
| "it matters to me…" (elevator, parking, brick, new build, Act 16, furnished, discount) | gives them more weight in the score              |
| "brick / panel / EPK only"                                                            | filters by construction type                     |
| "best deals first"                                                                    | sorts by score (otherwise newest first)          |
| "watch this" / "notify me"                                                            | saves the search and later shows only new listings |

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

| Service | What it does |
|---|---|
| `imotko` | the agent (AgentOS) at http://localhost:7777. You chat with it through [os.agno.com](https://os.agno.com) by pointing it at this address. |
| `watch` | checks saved searches every hour. If `NTFY_TOPIC` is set in `.env`, new listings arrive as notifications. |

Good to know:

- 💾 The databases (`imot.db` and `imot_history.db`) live in the `data/` folder and survive restarts and updates. Don't delete them if you care about saved searches and price history.
- 🔑 `.env` is not baked into the image; it is read at startup.
- 🔄 New version of the code: `git pull && docker compose up -d --build`.
- 📜 Logs: `docker compose logs -f imotko` (or `watch`).
- 🛑 Stop: `docker compose down` (data is kept).

If you run it on a server on the internet, don't expose port 7777 to the world without protection (a reverse proxy with a password, or a VPN).

---

## ⚙️ Configuration (everything is optional)

Everything is set in `.env`:

| Variable                     | What it is for                                                       | Default                                  |
| ---------------------------- | -------------------------------------------------------------------- | ---------------------------------------- |
| `OPENROUTER_API_KEY`         | your OpenRouter key (**required**)                                   | -                                        |
| `IMOT_MODEL`                 | the main model                                                       | `google/gemma-4-31b-it:free`             |
| `IMOT_FALLBACK_MODEL`        | fallback, used when the main one returns 429                         | `nvidia/nemotron-3-ultra-550b-a55b:free` |
| `IMOT_VISION_MODEL`          | model for photos (must be multimodal)                                | `google/gemma-4-31b-it:free`             |
| `IMOT_VISION_FALLBACK_MODEL` | fallback for photos                                                  | `nvidia/nemotron-3.5-super-vl:free`      |
| `WATCH_MIN_SCORE`            | minimum score a new listing needs in order to be flagged             | `20`                                     |
| `NTFY_TOPIC`                 | ntfy.sh topic for notifications                                      | disabled                                 |

> 💡 Free models sometimes return 429 ("I'm overloaded"). That is why there is a fallback - don't panic if an answer is slow.

---

## 🔌 Want another site or another notification channel?

You don't touch the rest of the code, you only subclass:

- new listings site (homes.bg, alo.bg...) -> a class that extends `ListingSource` in `imotko/sources/`, then register it in `imotko/sources/__init__.py`;
- new channel (Telegram, email...) -> a class that extends `Notifier` in `imotko/notify.py`. You only need the `send` method.

Price history and saved searches are stored in `imot_history.db` (in Docker, in `data/`), outside of git.

---

## ⚠️ Honest disclaimer, so you don't get upset later

- Imotko is a **helper, not an oracle**. The score is a guideline, not a verdict. It does not replace viewing the property in person.
- Photo analysis is "by eye". The size of a fridge, for example, is approximate.
- The data comes from the public pages of imot.bg. Don't send requests more often than needed, and be a good citizen.

Good luck with the search, and may you find a home, not just a "good investment"! 🏡
