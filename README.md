# Kryostat

A fast, modern app for monitoring, controlling and optimizing Windows 10/11 (x64).

![Kryostat](assets/logo256.png)

## Features
​
| # | Section | What it does |
|---|---------|--------------|
| 1 | **Processes and services** | A task manager that shows both running processes and **all Windows services, including stopped ones**. Filters: processes only / services only / running / not running / critical / third-party. |
| 2 | **Critical highlighting** | Processes and services critical for Windows are shown in bold red, and ending them is blocked. |
| 3 | **Programs** | All installed programs (registry Uninstall keys for all users, 32- and 64-bit, current user) and Microsoft Store apps, with search and uninstall (regular or silent). |
| 4 | **Startup** | Scans the registry (HKCU/HKLM Run, RunOnce, WOW6432Node), startup folders and scheduled tasks. **Duplicates** (the same program registered several times) are marked amber with ×N. |
| 5 | **Disabling startup entries** | With a checkbox or a button. Changes are **reversible** (the entry is moved to `HKxx\Software\Kryostat\DisabledStartup`) and saved to the config. "Remove duplicates" keeps one entry per program. |
| 6 | **Resource limits** | Per program: max RAM and max CPU (Windows **Job Objects**, hard cap), number of available cores, CPU priority, low disk priority, network speed limit (**QoS policy**). A rule is bound to the `.exe` name and applied to new launches automatically. System-wide: max CPU %, Turbo Boost, power plans, a shared budget for third-party processes. |
| 7 | **Windows updates** | Every day automatically extends the update pause by +N days so Windows doesn't restart your PC. |
| 8 | **Kryostat autostart** | Starts with Windows (`KryostatBoot` scheduled task with admin rights, or `HKCU\...\Run` without them) and immediately ends every program you disabled in Startup. |
| 9 | **Windows optimization** | 48 tweaks that run admin commands (`sc config DiagTrack start= disabled`, `sc stop DiagTrack`, …) and registry edits. Every tweak has a **rollback command**. |
| 10 | **Resource monitor** | Per-core CPU, RAM, disk read/write, network, drives, top consumers by CPU / memory / disk / network connections. |
| 11 | **Computer** | System and hardware info, uptime, power on/off history, program launch statistics (own and Windows UserAssist), power: restart, shut down, BIOS/UEFI, safe mode, scheduled shutdown. |
| 12 | **Antivirus** | Microsoft Defender: status, real-time protection, settings, CPU load limit, exclusions, scans (quick / full / folder / offline), signatures, detected threats. |
| 13 | **Registry** | Registry editor with a lazy-loading tree, all main value types, background search, favorites, `.reg` export/import and a backup of the key before every change. |
| 14 | **Terminal** | PowerShell / pwsh / cmd / Git Bash tabs running "as is", "as administrator" or "without admin rights", plus a log of Kryostat's own commands. |
​
Also: tray icon, background mode, activity log, 5 base themes × 12 accent colors (or your own HEX), automatic interface language.
​
## Interface, themes and language
​
- **Ctrl+K** — command palette (search pages and actions), **Ctrl+1…9** — jump to a page, **Ctrl+B** — compact sidebar.
- Settings → "Appearance": base (Midnight, Graphite, AMOLED, Ocean, Light) and accent color (12 presets or a custom one).
- The interface language follows the Windows display language. If there is no translation for it, English is used. To choose manually: Settings → "Interface language".
- Available languages: English, Russian.
​
### Adding a translation
​
Create `assets/locales/<code>.json` (for example `de.json`) using `en.json` as a template. Keys are the original Russian strings:
​
```json
{"meta": {"name": "German", "native": "Deutsch", "code": "de"},
 "strings": {"Настройки": "Einstellungen"}}
```
​
To get a list of untranslated strings, run the app with the environment variable `KRYOSTAT_I18N_MISSING=missing.txt`.
​
Pull requests with new languages are welcome.
​
## Requirements
​
- Windows 10 / 11, 64-bit
- Python 3.10–3.13 (for building only) — https://www.python.org/downloads/
- Inno Setup 6 (for the installer only) — https://jrsoftware.org/isdl.php
​
## Download
​
Ready-to-use builds are available on the [Releases](../../releases) page.
​
## Building
​
Clone the repository (or download the ZIP), open the folder and run:
​
```bat
build.bat
```
​
The script creates a virtual environment, installs dependencies and builds the `.exe` and the installer.
​
Output:
- `dist\Kryostat\Kryostat.exe` — portable build
- `dist\KryostatSetup.exe` — installer
​
### Manual build
​
```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
​
:: 1) application
pyinstaller --noconfirm --clean Kryostat.spec
​
:: 2) installer
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```
​
### Single file instead of a folder (starts slower)
​
```bat
pyinstaller --noconfirm --clean --onefile --windowed --uac-admin ^
  --name Kryostat --icon assets\kryostat.ico ^
  --version-file version_info.txt ^
  --add-data "assets;assets" main.py
```
​
### Run without building (development)
​
```bat
run_dev.bat
```
​
## Command-line options
​
| Option | Purpose |
|--------|---------|
| `--boot` | Autostart mode: ends disabled programs, applies tweaks and limits, extends the update pause |
| `--minimized` | Start minimized to tray |
| `--no-elevate` | Don't request UAC again |
| `--restarted` | Restart "as administrator": wait for the old instance to close |
| `--register-task` / `--unregister` | Service modes for the installer: create / remove autostart and exit |
​
## Where data is stored
​
`C:\ProgramData\Kryostat\`: `config.json`, log `kryostat.log`, program statistics `usage.json`, registry backups `registry-backups\`.
​
## Important
​
- The app requests administrator rights — without them, service management, update policies and limits for system processes are unavailable.
- Windows itself limits the update pause to **35 days**. When the limit is reached, install the pending updates once and the countdown starts over. Fully disabling security updates is not recommended.
- The QoS network limit works for apps whose traffic comes from the process itself; traffic through system services (e.g. BITS) isn't caught by a per-.exe policy.
- All tweaks are reversible with "Roll back checked". Creating a system restore point before applying many tweaks is recommended.
​
## License
​
[MIT](LICENSE)
​
