; VectorRelay for Windows - per-user installer (no admin rights). build.py compiles it with:
;   iscc /DAppVersion=1.0.0 /DSourceDir=<dist\VectorRelay> /DOutputDir=<dist> /DIconFile=<build\vectorrelay.ico> vectorrelay.iss
; Installs to %LOCALAPPDATA%\Programs\VectorRelay, adds VectorRelay to the Start menu (and optionally the desktop) and lists it
; in Settings > Apps. The window keeps its data (what you saved in it) in %LOCALAPPDATA%\VectorRelay, which
; uninstalling leaves in place so a reinstall finds it again.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "dist\VectorRelay"
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif
#ifndef IconFile
  #define IconFile "build\vectorrelay.ico"
#endif

[Setup]
AppId={{E81247BA-698B-569E-A6DE-E1E1C366EEFB}
AppName=VectorRelay
AppVersion={#AppVersion}
AppVerName=VectorRelay {#AppVersion}
AppPublisher=Juwen Lie
AppPublisherURL=https://vector-relay.pages.dev/
AppSupportURL=https://github.com/juw12/vector-relay
DefaultDirName={autopf}\VectorRelay
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=VectorRelay-Windows-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\VectorRelay.exe
UninstallDisplayName=VectorRelay
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\VectorRelay"; Filename: "{app}\VectorRelay.exe"; Comment: "See and steer the AI chats on your own PC from your phone"
Name: "{userdesktop}\VectorRelay"; Filename: "{app}\VectorRelay.exe"; Comment: "See and steer the AI chats on your own PC from your phone"; Tasks: desktopicon

[Run]
Filename: "{app}\VectorRelay.exe"; Description: "{cm:LaunchProgram,VectorRelay}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\VectorRelay.exe"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopVectorRelay"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  { Updating: close an open VectorRelay window first so its files can be replaced. }
  if FileExists(ExpandConstant('{app}\VectorRelay.exe')) then
    Exec(ExpandConstant('{app}\VectorRelay.exe'), '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
