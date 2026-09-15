# F1 Race Dashboard

I built this dashboard to make reviewing a completed Formula 1 race easier. It pulls timing and classification data through FastF1 and turns it into a straightforward post-race overview.

The project is focused on what is useful after the chequered flag rather than trying to act as a live timing screen.

## Features

- Final classification, gaps, points, and finishing status
- Starting grid and positions gained or lost
- Winner, podium, fastest lap, biggest mover, and retirement summary
- Grand Prix and sprint archive
- Driver and team form across recent rounds
- Driver comparisons and round-by-round results
- Circuit profiles and recent race history

Only completed sessions are shown. A short post-race buffer is used so an unfinished or provisional classification is not presented as final.

## Built with

- Python
- Flask and Jinja
- FastF1
- pandas
- Gunicorn
- Render

## Running locally

Create a virtual environment and install the dependencies:

```powershell
python -m venv venv
./venv/Scripts/Activate.ps1
pip install -r requirements.txt
```

On macOS or Linux, activate it with `source venv/bin/activate` instead.

Start the app:

```bash
python app.py
```

Then open [http://localhost:5000](http://localhost:5000).

To open a specific race, pass the year, round, and session type in the URL:

```text
http://localhost:5000/?year=2025&round=14&session_type=Race
```

## API

The dashboard data is also available as JSON:

```text
GET /api/data
```

It accepts the same `year`, `round`, and `session_type` query parameters as the main dashboard.

## Notes

- This is an unofficial project and is not affiliated with Formula 1.
- Data availability depends on FastF1 and its upstream timing sources.
- The analysis is retrospective. The dashboard does not currently publish race-outcome predictions.

## License

[MIT](./LICENSE)
