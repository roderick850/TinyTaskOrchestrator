; ─── AutoPulsar Installer Script ───
; Compila con: ISCC.exe AutoPulsar.iss

#define MyAppName "AutoPulsar"
#define MyAppVersion "1.2.1"
#define MyAppPublisher "Roderick"
#define MyAppURL "https://github.com/roderick850/AutoPulsar"
#define MyAppExeName "AutoPulsar.exe"

[Setup]
AppId={{B4F8A1D2-7C3E-4A9F-8E6B-1D5C2F3A8B9E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; El instalador necesita admin para escribir en Program Files
PrivilegesRequired=admin
OutputDir=.\dist
OutputBaseFilename=AutoPulsar_Installer
SetupIconFile=app_icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Desinstalador
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el &Escritorio"; GroupDescription: "Accesos directos adicionales:"; Flags: checkedonce

[Files]
; El ejecutable principal
Source: "dist\AutoPulsar.exe"; DestDir: "{app}"; Flags: ignoreversion
; Manual
Source: "manual.html"; DestDir: "{app}"; Flags: ignoreversion
; Imágenes del manual
Source: "manual_images\*"; DestDir: "{app}\manual_images"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Acceso directo en el Menú Inicio
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
; Manual en el Menú Inicio
Name: "{group}\Manual de AutoPulsar"; Filename: "{app}\manual.html"
; Desinstalador en el Menú Inicio
Name: "{group}\Desinstalar AutoPulsar"; Filename: "{uninstallexe}"
; Acceso directo en el Escritorio (opcional)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Ejecutar la app al terminar la instalación (opcional)
Filename: "{app}\{#MyAppExeName}"; Description: "Ejecutar AutoPulsar"; Flags: nowait postinstall skipifsilent unchecked

[Code]
// Verificar que no esté corriendo antes de instalar
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  // Intentar cerrar la app si está corriendo
  if CheckForMutexes('AutoPulsar') then
  begin
    if MsgBox('AutoPulsar está en ejecución. ¿Cerrarla para continuar con la instalación?',
               mbConfirmation, MB_YESNO) = IDYES then
    begin
      // Intentar cerrar suavemente
      Exec('taskkill', '/f /im AutoPulsar.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end
    else
    begin
      Result := False;
      Exit;
    end;
  end;
  Result := True;
end;
