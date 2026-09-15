# F1 Post-Race Review

A focused Formula 1 dashboard for reviewing completed Grands Prix and sprints. It uses FastF1 timing data to present the official result and the factual context around it.

The dashboard intentionally does not present itself as a live race companion. New Grands Prix appear only after a conservative post-race buffer so incomplete classifications are not shown as final results.

## What the dashboard keeps

### Completed-race review

- Official classification and gaps
- Starting grid and positions gained or lost
- Best lap, classification status, and points for every driver
- Winner, podium, fastest lap, biggest mover, race distance, and non-finisher summary
- Historical Grand Prix and sprint selection

### Supporting context

- Season form and teammate comparisons
- Driver form and round-by-round results
- Circuit characteristics and recent history

## Removed live/pre-race features

- Pit-now recommendations and simulated rejoin positions
- Pit-window status
- Active battle detection
- DRS and overtake predictions
- Live auto-refresh controls
- Standalone practice and qualifying dashboards
- Weekend outlook and pre-weekend briefing
- Public data-quality diagnostics
- Heuristic pace-loss alerts
- Heuristic pre-race forecast and accuracy review

## Architecture

```text
Completed race request
        |
        v
FastF1 session + official results
        |
        +--> final classification and race summary
        |
        v
Flask + Jinja post-race dashboard
```

The app remains on-demand and does not require an always-running live timing collector.

## Prediction policy

The current dashboard contains no race-outcome predictions. Derived values such as form, average grid movement, and consistency summarize completed sessions; circuit labels describe a typical historical profile. If predictions are added later, they should come from a versioned machine-learning pipeline with time-ordered backtesting, uncertainty estimates, and a clearly documented training cutoff.

## Project structure

```text
f1-dashboard/
|-- app.py                 # Flask routes and post-race analysis orchestration
|-- data_handler.py        # FastF1 loading, completed-race selection, classification
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

`GET /api/data` returns the completed-race classification and race summary as JSON. It accepts the same `year`, `round`, and `session_type` query parameters as the main page.

## Data notes

- FastF1 is unofficial and depends on upstream Formula 1 timing data.
- The default page selects the latest Grand Prix whose scheduled start is at least four hours in the past.
- Manually selected races and sprints are also blocked until their post-session safety window has elapsed.

## License

[MIT](./LICENSE)
