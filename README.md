# Road to 50 — Telegram-Bot (24/7 Backend)

Telegram-Bot als mobile Oberfläche für dein Content-System: Morgen-Briefing per Push,
Video-Upload direkt im Chat, KI-generierte Caption/Hook, Freigabe per Knopfdruck.
Läuft dauerhaft in der Cloud (Railway), unabhängig davon, ob dein Rechner oder Claude
gerade offen ist.

## Warum Railway

Getestet gegen Render und Fly.io: Render's kostenloser Tier schläft nach 15 Minuten
Inaktivität ein (30-60 Sek. Cold-Start — bei einem Chat-Bot spürbar und bei einem
6-Uhr-Cron-Trigger unzuverlässig), Fly.io hat 2026 keinen Free-Tier mehr. Railway läuft
auf dem Hobby-Plan (**$5/Monat**) durchgehend ohne Cold-Start und bringt native
Cron-Jobs mit — genau das, was wir für den täglichen 6-Uhr-Trendscan brauchen.

## Architektur

```
Telegram (dein Handy)
   │  Webhook
   ▼
FastAPI-App (Railway, 24/7)
   ├─ python-telegram-bot   → Chat-Logik, Inline-Buttons
   ├─ Anthropic API (Claude) → Hook/Caption-Generierung (Tool-Use + Pydantic-Validierung), Morgen-Briefing
   │    └─ Greger-Wissensbasis (data/greger_facts.json) → Fakten-Injection bei Education-Posts
   ├─ Postgres (Railway-Addon) → Status jedes Videos (Draft → Freigegeben → Gepostet)
   ├─ FFmpeg (app/storage.py::stitch_videos) → mehrere Clips -> ein 9:16-Reel
   ├─ /static/videos/{id}.mp4  → oeffentlicher Zwischenspeicher fuer Clips (app/storage.py)
   ├─ Instagram Graph API   → verdrahtet, aktiv sobald Business-Account eingerichtet ist (Schritt 2)
   └─ YouTube Data API v3   → Schritt 3, sobald OAuth eingerichtet ist
```

## Schritt 1: Bot lauffähig machen (kein Meta/YouTube-Zugang nötig)

Damit hast du sofort ein funktionierendes Telegram <-> Claude-System: Video schicken,
Beschreibung eingeben, KI-Vorschlag bekommen, freigeben. Das eigentliche Veröffentlichen
läuft automatisch mit, sobald du Schritt 2 (Instagram) erledigt hast — bis dahin bleibt
"Freigeben & Posten" ein reiner Status-Wechsel plus Hinweis zum manuellen Posten.

1. **Telegram-Bot erstellen**: In Telegram `@BotFather` öffnen → `/newbot` → Namen
   vergeben → den `TELEGRAM_BOT_TOKEN` kopieren.
2. **Deine Chat-ID herausfinden**: `@userinfobot` in Telegram öffnen, er zeigt dir
   deine ID direkt an → das ist `TELEGRAM_CHAT_ID` (schützt den Bot davor, dass jemand
   anderes ihn benutzt).
3. **Anthropic API-Key holen**: console.anthropic.com → API Keys → neuen Key erzeugen
   → `ANTHROPIC_API_KEY`.
4. **Code zu GitHub pushen**: dieses Verzeichnis (`telegram-bot/`) in ein neues
   GitHub-Repo pushen.
5. **Railway-Konto anlegen** auf railway.com, Hobby-Plan aktivieren ($5/Monat).
   "New Project" → "Deploy from GitHub repo" → das Repo auswählen.
6. **Postgres hinzufügen**: im Railway-Projekt "New" → "Database" → "PostgreSQL".
   Railway setzt `DATABASE_URL` automatisch als Env-Var für den Web-Service.
7. **Env-Vars setzen** (Railway → dein Service → Variables): `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`, `CRON_SECRET` (frei erfundenes Passwort),
   `WEBHOOK_BASE_URL` = die von Railway vergebene Domain, z.B.
   `https://road-to-50-production.up.railway.app`.
8. **Deploy abwarten**. Beim Start registriert die App den Telegram-Webhook automatisch
   (`app/main.py`, `lifespan`-Funktion) — kein manueller `setWebhook`-Call nötig.
9. **Test**: `/start` an deinen Bot schicken. Er antwortet mit dem Countdown und der
   heutigen Idee aus dem Redaktionsplan.

## Schritt 2: Instagram anbinden

1. Instagram-Account auf "Professionell" (Creator) umstellen (Instagram-App →
   Einstellungen → Konto).
2. Auf developers.facebook.com einen Meta-Developer-Account anlegen, neue App
   erstellen, Produkt "Instagram API setup with Instagram login" (Business Login for
   Instagram) hinzufügen — keine Facebook-Seite nötig.
3. Dich selbst im App-Dashboard als Account verknüpfen. Da die App nur deinen eigenen
   Account bedient, reicht **Standard Access** — kein App Review, keine
   Business-Verifizierung nötig.
4. Access Token + deine `ig_business_account_id` aus dem Dashboard holen, als
   `IG_ACCESS_TOKEN` / `IG_BUSINESS_ACCOUNT_ID` in Railway eintragen.
5. **Fertig** — sobald beide Env-Vars gesetzt sind, macht "Freigeben & Posten" im
   Telegram-Chat automatisch den kompletten Instagram-Publish (Video hochladen,
   Container erstellen, veröffentlichen). Kein weiterer Code nötig.

**Wie die öffentliche Video-URL zustande kommt (technischer Hintergrund):**
Instagram verlangt beim Publish eine öffentlich erreichbare Video-URL (kein direkter
Datei-Upload). Der Bot lädt den Clip beim Empfang direkt von Telegram herunter und
legt ihn lokal unter `static/videos/{post_id}.mp4` ab (`app/storage.py`); eine
FastAPI-`StaticFiles`-Route (`app/main.py`) macht ihn unter
`{WEBHOOK_BASE_URL}/static/videos/{post_id}.mp4` öffentlich abrufbar, bis Meta ihn
beim Publish abgeholt hat.

*Einschränkung:* Railways Dateisystem ist **nicht persistent** über Redeploys hinweg
(neuer Container = leeres Dateisystem). Für den Publish-Vorgang selbst ist das
unkritisch — Meta ruft das Video sofort ab. Falls du aber alle geposteten Clips
dauerhaft archivieren willst, oder falls es bei Redeploys mitten im Freigabe-Prozess
Probleme gibt, ist der nächste Schritt ein Umstieg auf Cloudflare R2 / S3: dafür
müsste nur `storage.py` ersetzt werden (Upload statt lokalem Speichern), der Rest
des Codes bleibt gleich. Sag Bescheid, falls du das priorisieren willst.

## Schritt 3: YouTube anbinden

1. In der Google Cloud Console ein Projekt anlegen, "YouTube Data API v3" aktivieren.
2. OAuth-Client-ID (Typ "Desktop App") erstellen, als `yt_client_secret.json`
   herunterladen.
3. Einmalig lokal den OAuth-Consent-Flow durchlaufen (Skript folgt), um
   `yt_token.json` zu erzeugen — danach läuft der Upload automatisch, das Token wird
   selbstständig erneuert.
4. Beide Dateien sicher in Railway hinterlegen (z.B. als Base64-codierte Env-Var, die
   beim Start in eine Datei geschrieben wird).

## Dr.-Greger-Wissensbasis (Education-Posts)

`data/greger_facts.json` enthält paraphrasierte Kernaussagen aus Michael Gregers
*How Not to Age* (Zellgesundheit, Autophagie, Gelenke & Sehnen, Ernährung), thematisch
getaggt (u.a. nach Skill: Handstand, Human Flag, Seilspringen).

Ablauf: Wenn der heutige Redaktionsplan-Eintrag als `Education` markiert ist, zieht
`app/telegram_bot.py` beim Erstellen einer Caption automatisch passende Fakten über
`app/greger.py` und mischt sie in den System-Prompt (`app/ai.py::generate_caption`).
Die KI entscheidet dann selbst, ob und wie sie die Fakten natürlich einbaut — es wird
nichts erzwungen, und es werden keine zusätzlichen Zahlen/Studien erfunden, die nicht
in der JSON stehen.

*Wichtig:* Die Fakten sind Zusammenfassungen aus öffentlich zugänglichen
Buchbesprechungen/NutritionFacts.org, keine wörtlichen Zitate aus dem Buch. Bei
Bedarf (z.B. für einen Post mit konkreten Studienzahlen) lohnt sich eine Gegenprüfung
im Buch selbst, bevor du die Zahl als harten Fakt kommunizierst. Weitere Fakten lassen
sich einfach durch neue Einträge in `data/greger_facts.json` ergänzen (Format siehe
vorhandene Einträge: `id`, `kategorie`, `tags`, `fact`).

## Multi-Clip-Stitching (mehrere Clips -> ein Reel)

Du kannst 2-4 (bis zu `MAX_CLIPS_PER_BATCH = 6`) kurze Clips hintereinander schicken
— als Telegram-Album (Mehrfachauswahl -> Senden) oder einfach einzeln kurz
nacheinander. Der Bot sammelt sie pro Chat (`app/telegram_bot.py::handle_video`)
und wartet `CLIP_BATCH_DEBOUNCE_SECONDS` (aktuell 4 Sekunden) ohne weiteren
eingehenden Clip, bevor er die Serie als vollständig betrachtet. Das deckt sowohl
echte Telegram-Mediengruppen (`media_group_id`, treffen praktisch gleichzeitig
ein) als auch einzeln, aber schnell hintereinander gesendete Videos ab, ohne dass
zwischen beiden Fällen unterschieden werden muss — sendest du die Clips langsamer
als alle 4 Sekunden, werden sie stattdessen als separate Einzel-Posts behandelt.

Danach fügt `app/storage.py::stitch_videos()` die Clips per FFmpeg chronologisch
zu einem einzigen 9:16-Reel (1080×1920) zusammen: jeder Clip wird einzeln skaliert
und zentriert/letterboxed, damit unterschiedliche Auflösungen/Ausrichtungen kein
Problem sind; Clips ohne Tonspur (z.B. stumme Aufnahmen) bekommen automatisch eine
stille Ersatzspur, damit das Zusammenfügen nicht an einer fehlenden Audiospur
scheitert. Bei genau einem Clip entfällt der FFmpeg-Umweg (kein Qualitätsverlust
durch unnötiges Neu-Encodieren).

Danach fragt der Bot gezielt nach einer Beschreibung *pro Clip* (z.B. "Clip 1:
Sturz, Clip 2: 8 Sekunden gehalten") — `app/ai.py::generate_caption()` bekommt
mitgeteilt, dass es sich um eine Clip-Sequenz handelt, und baut Hook/Caption
dramaturgisch passend dazu auf (Vorher/Nachher, Fail-dann-Erfolg, Fortschritt),
statt die Clips isoliert zu behandeln.

**FFmpeg auf Railway:** Railways Standard-Nixpacks-Build für Python bringt FFmpeg
*nicht* automatisch mit (das war eine falsche Annahme im ursprünglichen Auftrag).
Die Datei `nixpacks.toml` im Projekt-Root sorgt dafür, dass FFmpeg beim Build
zusätzlich installiert wird:

```toml
[phases.setup]
nixPkgs = ["ffmpeg"]
```

Das reicht i.d.R. automatisch — Railway erkennt `nixpacks.toml` beim Build ohne
weitere Konfiguration. Fehlt FFmpeg trotzdem (z.B. bei einem anderen Build-Setup),
meldet `stitch_videos()` das mit einer klaren Fehlermeldung im Telegram-Chat statt
stillschweigend fehlzuschlagen.

## Täglicher Trendscan / Morgen-Briefing einrichten

1. Railway → dein Service → Settings → "Cron Schedule" aktivieren.
2. Schedule: `0 6 * * *` (06:00 Uhr täglich, Railway nutzt UTC — ggf. auf deine
   Zeitzone umrechnen).
3. Befehl: ruft `POST {WEBHOOK_BASE_URL}/cron/morning-briefing?secret={CRON_SECRET}`
   auf, z.B. via `curl -X POST "$WEBHOOK_BASE_URL/cron/morning-briefing?secret=$CRON_SECRET"`.

Aktuell nutzt das Briefing nur den Redaktionsplan (keine Live-Trendsuche). Der nächste
Ausbauschritt: in `app/ai.py::morning_briefing()` einen Web-Search-Aufruf einhängen,
damit Claude tagesaktuelle Trends mit einbezieht.

## Lokal testen

```bash
cd telegram-bot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Werte eintragen
uvicorn app.main:app --reload
```

Für lokale Tests ohne öffentliche URL: `WEBHOOK_BASE_URL` leer lassen (Webhook wird
dann nicht registriert) und stattdessen mit `ngrok` o.ä. tunneln, oder die
Handler-Funktionen direkt per Skript testen.

## Production Hardening (diese Iteration)

Drei Robustheits-Fixes für den Live-Betrieb:

1. **Strukturierte KI-Ausgabe statt naivem `json.loads()`**: `app/ai.py` fordert
   Claude jetzt per Anthropic Tool-Use (`tool_choice` erzwingt den Aufruf von
   `emit_posting`) zu einer strukturierten Antwort auf, die zusätzlich mit einem
   Pydantic-Modell (`Posting`) validiert wird. Schlägt die Validierung fehl (z.B.
   fehlendes Feld, falsche Kategorie), bekommt Claude den konkreten Fehler
   zurückgespielt und einmal die Chance zur Korrektur, bevor ein klarer
   `CaptionGenerationError` an den Telegram-Chat gemeldet wird — kein stiller
   Absturz mehr bei einer unsauberen Antwort.
2. **Interaktions-Status in Postgres statt In-Memory**: `PENDING_ACTION` (ein
   Python-Dict im Prozessspeicher) ist weg. Der Status, worauf der Bot als
   Nächstes wartet (`awaiting_description` / `awaiting_revision`), steckt jetzt
   direkt in der `video_posts`-Tabelle (neue Spalten `chat_id`, `pending_action`,
   siehe `db.get_pending_post()` / `db.set_pending_action()`). Ein Railway-Neustart
   mitten im Dialog (z.B. nach einem Deploy) verliert den Kontext dadurch nicht
   mehr.
3. **Video-Existenzprüfung + Cleanup**: Vor dem Instagram-Publish prüft
   `app/telegram_bot.py` jetzt per `storage.video_exists()`, ob die Datei
   tatsächlich noch lokal da ist (relevant, weil Railways Dateisystem Redeploys
   nicht übersteht) — fehlt sie, kommt sofort "Videodatei nicht gefunden, bitte
   erneut hochladen." statt eines kryptischen Fehlers von der Meta-API. Nach
   erfolgreichem Publish löscht `storage.cleanup_video()` den lokalen Clip
   automatisch, damit der begrenzte Speicher nicht volläuft.

## Falls du bereits einen Railway-Postgres mit Daten hast

Dieses Update fügt der `video_posts`-Tabelle eine weitere neue Spalte hinzu
(`clip_count`, zusätzlich zu `video_path`/`chat_id`/`pending_action` aus der
vorherigen Iteration). `db.init_db()` legt Tabellen nur an, wenn sie noch nicht
existieren — bestehende Tabellen werden nicht automatisch verändert. Falls du
schon Video-Posts in der Datenbank hast: entweder die Tabelle einmalig droppen
(Datenverlust, aber bei einem frischen Testsystem meist unkritisch) oder manuell
ausführen, bevor du neu deployst:

```sql
ALTER TABLE video_posts ADD COLUMN video_path VARCHAR;
ALTER TABLE video_posts ADD COLUMN chat_id VARCHAR;
ALTER TABLE video_posts ADD COLUMN pending_action VARCHAR;
ALTER TABLE video_posts ADD COLUMN clip_count INTEGER DEFAULT 1;
```

## Nächste Ausbauschritte (bewusst noch nicht gebaut)

- **Live-Trendscan**: Web-Suche (z.B. Perplexity/Tavily) in `morning_briefing()`
  einhängen, damit das Morgen-Briefing tagesaktuelle Trends mit einbezieht.
- **YouTube-Anbindung** (Schritt 3): OAuth-Flow + Upload-Verdrahtung analog zu
  Instagram.
- **Persistenter Video-Speicher**: Umstieg von lokalem Railway-Dateisystem auf
  Cloudflare R2 / S3, falls Redeploy-Timing zum Problem wird oder Clips dauerhaft
  archiviert werden sollen (siehe Hinweis unter "Schritt 2: Instagram anbinden").
- **Video-Analyse statt Text-Beschreibung**: aktuell beschreibst du den Clip kurz per
  Text. Eine echte Videoanalyse (Bewegungserkennung, Haltungscheck) bräuchte ein
  Vision-Modell — lohnt sich erst, wenn der Text-Workflow im Alltag läuft.
- **Animierte Untertitel**: bewusst nicht selbst gebaut — stattdessen einen
  bestehenden Dienst (z.B. Untertitel-API) einhängen, sobald der Bedarf da ist.
  (Das reine Zusammenfügen mehrerer Clips per FFmpeg ist seit dieser Iteration
  bereits gebaut, siehe "Multi-Clip-Stitching" oben.)
- **Auto-Kommentar-Antworten**: rechtlich zu beachten — Meta verlangt für den
  deutschen Markt eine Offenlegungspflicht bei automatisierten Chat-Antworten.
- **Telegram Mini App**: für ein visuelles Analytics-Dashboard, sobald der
  Basis-Workflow steht.
