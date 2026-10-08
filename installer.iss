; Inno Setup — установщик Kryostat
#define AppName     "Kryostat"
#define AppVersion  "1.0.0"
#define AppExe      "Kryostat.exe"

[Setup]
AppId={{B7C1E4A2-9F3D-4C77-9A41-5E2C8A1D6F03}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Kryostat
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=KryostatSetup
SetupIconFile=assets\kryostat.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
; не даёт ставить поверх запущенного Kryostat (имя мьютекса совпадает с app/instance.py)
AppMutex=Global\KryostatSingleInstance
CloseApplications=yes
RestartApplications=no
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"
Name: "autostart";  Description: "Запускать Kryostat при входе в Windows"; GroupDescription: "Дополнительно:"

[Files]
Source: "dist\Kryostat\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent
; Задача создаётся самим приложением через XML: без лимита «остановить через 72 часа» и без запрета запуска от батареи
Filename: "{app}\{#AppExe}"; Parameters: "--register-task --minimized"; Flags: runhidden waituntilterminated; Tasks: autostart

[UninstallRun]
Filename: "{app}\{#AppExe}"; Parameters: "--unregister"; Flags: runhidden waituntilterminated; RunOnceId: "DelAutostart"
Filename: "taskkill"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
Type: filesandordirs; Name: "{commonappdata}\Kryostat"
