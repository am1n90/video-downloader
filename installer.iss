; ============================================================
;  Video Downloader — Inno Setup 6
;  Per-user установка (без UAC): {localappdata}\Programs\VideoDownloader
;  Данные пользователя (%LOCALAPPDATA%\VideoDownloader) при
;  деинсталляции НЕ удаляются (опционально — задача внизу).
; ============================================================

#define MyAppName "Video Downloader"
#define MyAppVersion "1.0.6"
#define MyAppPublisher "VideoDownloader"
#define MyAppExeName "VideoDownloader.exe"
#define MyAppId "{{8F5C2A71-3D64-4B7E-9A1F-6C2E8D4B7A90}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\VideoDownloader
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=VideoDownloader-Setup-1.0.6
SetupIconFile=assets\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Установщик не подписан — MinVersion Win10
MinVersion=10.0
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\VideoDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Run]
; Автообновление: setup /AUTOLAUNCH → приложение стартует после тихой установки
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: LaunchAfterUpdate
; Обычная установка: галочка «Запустить программу»
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent; Check: not LaunchAfterUpdate

[UninstallDelete]
; Удаляем только файлы приложения; данные в {localappdata}\VideoDownloader
; остаются (задача deletedata ниже — опционально).
Type: filesandordirs; Name: "{app}"

[Code]
var
    DeleteUserData: Boolean;

function LaunchAfterUpdate: Boolean;
begin
    // /AUTOLAUNCH передаётся при автообновлении — приложение стартует
    // сразу после тихой установки (см. updater.apply_update)
    Result := Pos('/AUTOLAUNCH', UpperCase(GetCmdTail())) > 0;
end;

function InitializeUninstall(): Boolean;
begin
    Result := True;
    DeleteUserData := False;
    // В интерактивном режиме спрашиваем; в /VERYSILENT данные сохраняем
    if not UninstallSilent then begin
        if MsgBox(
               'Удалить также данные пользователя (настройки и историю загрузок)?',
               mbConfirmation, MB_YESNO) = IDYES then
            DeleteUserData := True;
    end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
    if (CurUninstallStep = usUninstall) and DeleteUserData then begin
        DelTree(ExpandConstant('{localappdata}\VideoDownloader'), True,
                True, True);
    end;
end;
