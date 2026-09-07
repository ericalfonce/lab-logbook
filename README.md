# Computer Laboratory Usage Logbook

A lightweight, localhost-based logbook that replaces the paper lab logbook.
Log who used which computer, when, and for how long — no pre-registration,
no IDs, no accounts required for regular users.

## Features

- **One-page check-in** — name (+ class if student) + tap an available computer + big Check In button.
- **Tap-to-check-out** — check out from the active session list, or auto-logout after a configurable idle time (default 120 min).
- **Live status board** — every computer shown as Free / Occupied / Under Maintenance.
- **History** — searchable/filterable log of all past sessions (name, class/staff, PC, time in, time out, duration).
- **Reports** — daily/weekly/monthly totals; export to **Excel** (.xlsx) and **PDF**.
- **Admin panel** — password-protected; mark computers under maintenance, edit/delete wrong log entries, add/remove computers, set idle timeout.

## How it works

- Anyone can check in: typing a new name simply creates a new log entry — validation against a user list is intentionally skipped.
- Staff check in with just their name; students add their class (e.g. "Form 3A", "Diploma Yr 2").
- All data lives in a single SQLite file (`logbook.db`) — backup by copying that one file.
- Runs fully offline on localhost / LAN. No internet, no cloud, no external services.

## Requirements

- Python 3.9+ on Windows, macOS, or Linux.

## Run it

Windows:
```
start.bat
```

Or on any platform:
```
pip install -r requirements.txt
python app.py
```

Then open http://localhost (defaults to 30 computers; override with `LAB_NUM_COMPUTERS`).

## Configuration (environment variables)

| Variable                | Default   | Description                          |
|-------------------------|-----------|--------------------------------------|
| `LAB_ADMIN_PASSWORD`    | `admin123`| Admin panel password. **Change it.** |
| `LAB_IDLE_TIMEOUT`      | `120`     | Auto-logout idle minutes             |
| `LAB_NUM_COMPUTERS`     | `30`      | Computers created on first run       |
| `LAB_PORT`              | `80`      | Port to listen on                    |

## Admin access

- Login link is in the top-right of every page (`Admin`).
- Default password: `admin123` — set `LAB_ADMIN_PASSWORD` before first run.
- Session-based login, stored server-side only.

## Project structure

```
app.py              # Flask app: routes, DB, exports
requirements.txt    # flask, openpyxl, reportlab
static/style.css    # single stylesheet, no external dependencies
templates/          # Jinja2 templates (check-in, history, reports, admin, login)
```

## Tech stack

- Flask + SQLite (single-file DB)
- Jinja2 templates + one self-contained CSS file (no CDN, no frameworks)
- openpyxl (Excel export) and reportlab (PDF export)

## License

MIT