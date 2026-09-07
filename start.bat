@echo off
cd /d "%~dp0"
echo Installing dependencies (first run only)...
python -m pip install -r requirements.txt --quiet
echo Starting Lab Logbook at http://localhost
start "" http://localhost
python app.py
pause