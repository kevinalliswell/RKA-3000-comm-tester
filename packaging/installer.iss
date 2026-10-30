#define AppVersion "0.2.0-rc1"

[Setup]
AppId={{B83964BB-622D-4F54-B813-2C5181EAD091}
AppName=RKA-3000 通信测试工具
AppVersion={#AppVersion}
AppPublisher=Kevin Alliswell
DefaultDirName={localappdata}\Programs\RKA Comm Tester
DefaultGroupName=RKA Comm Tester
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
OutputDir=..\dist
OutputBaseFilename=RKA-Comm-Tester-Setup-{#AppVersion}-win-x64
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=RKA-3000 通信测试工具

[Files]
Source: "..\dist\RKACommTester\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\examples\*.json"; DestDir: "{app}\examples"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\RKA-3000 通信测试工具"; Filename: "{app}\RKACommTester.exe"

[Run]
Filename: "{app}\RKACommTester.exe"; Description: "启动 RKA-3000 通信测试工具"; Flags: nowait postinstall skipifsilent
