# Trading Bot Professional - Setup Guide

## Automatische Installation

Das einfachste Setup mit einem einzigen Befehl:

```bash
./setup.sh
```

Das Skript führt automatisch folgende Schritte aus:
1. Prüft Python 3.12+ Installation
2. Erstellt Virtual Environment
3. Installiert alle Dependencies
4. Konfiguriert VS Code
5. Verifiziert die Installation

## Voraussetzungen

### Python 3.12 oder höher

Überprüfe deine Python-Version:
```bash
python3 --version
```

Falls Python 3.12+ nicht installiert ist:

**macOS (Homebrew):**
```bash
brew install python@3.14
```

**Linux (apt):**
```bash
sudo apt update
sudo apt install python3.14 python3.14-venv
```

## Manuelle Installation

Falls du die Schritte manuell durchführen möchtest:

### 1. Virtual Environment erstellen
```bash
python3 -m venv venv
```

### 2. Dependencies installieren
```bash
./venv/bin/python3 -m pip install -r requirements.txt
```

### 3. VS Code konfigurieren
Erstelle `.vscode/settings.json`:
```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/venv/bin/python3"
}
```

## Bot starten

### Mit Virtual Environment Path:
```bash
./venv/bin/python3 main.py
```

### Mit aktiviertem Virtual Environment:
```bash
source activate.sh
python3 main.py
```

## Nützliche Skripte

### Dependencies aktualisieren
```bash
./update_dependencies.sh
```

Aktualisiert alle Pakete auf die neueste Version.

### Aufräumen
```bash
./clean.sh
```

Entfernt Virtual Environment und Cache-Dateien.

### Neuinstallation
```bash
./clean.sh
./setup.sh
```

## Troubleshooting

### Import-Fehler in VS Code

Falls VS Code die Imports nicht erkennt:

1. Öffne VS Code Command Palette (Cmd+Shift+P / Ctrl+Shift+P)
2. Suche nach "Python: Select Interpreter"
3. Wähle `./venv/bin/python3`
4. Starte VS Code neu

### Python Version zu alt

Das Projekt benötigt Python 3.12+. Bei älteren Versionen:

- **pandas-ta** kann nicht installiert werden (benötigt 3.12+)
- Der Bot funktioniert ohne pandas-ta, aber mit eingeschränkter Funktionalität

### Fehler beim Installieren von pandas-ta

pandas-ta ist optional und benötigt spezielle Dependencies (numba).
Bei Python 3.14+ kann es zu Kompatibilitätsproblemen kommen.

Das Setup-Skript versucht pandas-ta zu installieren, fährt aber auch ohne fort.

### Berechtigungsfehler

Falls Skripte nicht ausführbar sind:
```bash
chmod +x setup.sh update_dependencies.sh clean.sh activate.sh
```

## Verifikation

### Installierte Pakete anzeigen:
```bash
./venv/bin/python3 -m pip list
```

### Imports testen:
```bash
./venv/bin/python3 -c "import ccxt, pandas, numpy; print('✓ All imports work')"
```

### Python-Version prüfen:
```bash
./venv/bin/python3 --version
```

## VS Code Integration

Nach dem Setup sollte VS Code automatisch:
- Den richtigen Python-Interpreter verwenden
- Imports korrekt auflösen
- IntelliSense für alle Pakete bereitstellen

Falls nicht, reload das VS Code Window:
- Command Palette → "Developer: Reload Window"

## Dependencies

Hauptabhängigkeiten:
- **ccxt**: Exchange API Verbindungen
- **pandas**: Datenanalyse
- **numpy**: Numerische Berechnungen
- **pydantic**: Datenvalidierung
- **rich**: Terminal-UI
- **Flask**: Web-Dashboard
- **requests/aiohttp**: HTTP-Requests

Siehe `requirements.txt` für alle Details.

## Weitere Hilfe

Bei Problemen:
1. Prüfe die Fehlermeldungen im Terminal
2. Stelle sicher, dass Python 3.12+ installiert ist
3. Führe `./clean.sh` und dann `./setup.sh` aus
4. Prüfe die VS Code Output-Konsole (Python)
