#define AppVersion "1.0.0"
[Setup]
AppId={{B995DE03-2C89-4BF2-9BF6-94A5FC856351}
AppName=LANBridge Pro
AppVersion={#AppVersion}
DefaultDirName={autopf}\LANBridgePro
DefaultGroupName=LANBridge Pro
OutputDir=..\dist
OutputBaseFilename=LANBridgePro-Setup
SetupIconFile=..\src\lanbridge\resources\app.ico
UninstallDisplayIcon={app}\LANBridgePro.exe
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\LANBridgePro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\USER_GUIDE_AR.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LANBridge Pro"; Filename: "{app}\LANBridgePro.exe"
Name: "{autodesktop}\LANBridge Pro"; Filename: "{app}\LANBridgePro.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\LANBridgePro.exe"; Description: "Launch LANBridge Pro"; Flags: nowait postinstall skipifsilent runasoriginaluser
