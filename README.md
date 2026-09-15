# F1 Post-Race Review

A focused Formula 1 dashboard for reviewing completed Grands Prix and sprints. It uses FastF1 timing data to explain the final result, surface important pace-loss moments, and compare the pre-race model with what actually happened.

The dashboard intentionally does not present itself as a live race companion. New Grands Prix appear only after a conservative post-race buffer so incomplete classifications are not shown as final results.

## What the dashboard keeps

### Completed-race review

- Official classification and gaps
- Starting grid and positions gained or lost
- Best lap, classification status, and points for every driver
- Winner, podium, fastest lap, biggest mover, race distance, and non-finisher summary
- Notable isolated pace losses with pit and neutralized laps filtered where possible
- Pre-race projection accuracy against the official result
- Historical Grand Prix and sprint selection

### Supporting context

- Season form and teammate comparisons
- Driver form and round-by-round results
- Circuit characteristics and recent history

Practice and qualifying data are still used internally when rebuilding the original pre-race projection for the accuracy report. Their former standalone dashboard views were removed because the product is now centered on the completed-race story.

## Removed live/pre-race features

- Pit-now recommendations and simulated rejoin positions
- Pit-window status
- Active battle detection
- DRS and overtake predictions
- Live auto-refresh controls
- Standalone practice and qualifying dashboards
- Weekend outlook and pre-weekend briefing
- Public data-quality diagnostics

## Architecture

```text
Completed race request
        |
        v
FastF1 session + official results
        |
        +--> final classification and race summary
        +--> filtered pace-loss analysis
        +--> qualifying/practice context --> projection accuracy
        |
        v
Flask + Jinja post-race dashboard
```

The app remains on-demand and does not require an always-running live timing collector.

## Project structure

```text
f1-dashboard/
|-- app.py                 # Flask routes and post-race analysis orchestration
|-- data_handler.py        # FastF1 loading, completed-race selection, classification
|-- anomaly.py             # Retrospective pace-loss detection
|-- qualifying.py          # Internal qualifying analysis for forecast reconstruction
|-- practice.py            # Internal practice context for forecast reconstruction
|-- race_projection.py     # Rebuilds the pre-race forecast
|-- prediction_accuracy.py # Compares forecast with the official result
|-- season_form.py         # Recent driver and team form
|-- circuit_intel.py       # Circuit context and history
|-- driver_intel.py        # Driver-focused season review
|-- requirements.txt
|-- render.yaml
`-- templates/
    |-- dashboard.html
    |-- season.html
    |-- circuit.html
    `-- driver.html
```

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`.

Historical review example:

```text
http://localhost:5000/?year=2025&round=14&session_type=Race
```

## API

`GET /api/data` returns the completed-race classification, race summary, notable pace losses, and projection accuracy as JSON. It accepts the same `year`, `round`, and `session_type` query parameters as the main page.

## Data notes

- FastF1 is unofficial and depends on upstream Formula 1 timing data.
- The default page selects the latest Grand Prix whose scheduled start is at least four hours in the past.
- Manually selected races and sprints are also blocked until their post-session safety window has elapsed.
- Pace-loss entries are statistical signals, not confirmed incident classifications.

## License

[MIT](./LICENSE)
