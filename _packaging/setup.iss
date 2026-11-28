; 面试点评器 Inno Setup 安装脚本
; 作者：慕夕制作
#define MyAppName "面试点评器"
#define MyAppVersion "2.8.0"
#define MyAppPublisher "慕夕制作"
#define MyAppExeName "面试点评器.exe"
#ifndef SrcDir
  #define SrcDir "..\dist\面试点评器"
#endif
#ifndef OutputDir
  #define OutputDir "..\output"
#endif

[Setup]
AppId={{8F3A62C1-5B47-4C1E-9D0A-2E7C41B9F6A3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright=版权所有 (C) 2026 {#MyAppPublisher}
DefaultDirName={localappdata}\Programs\InterviewReviewer
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=面试点评器_安装程序_v2.8.0
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion=2.8.0.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoCopyright=版权所有 (C) 2026 {#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductTextVersion={#MyAppVersion}
CloseApplications=yes
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "{#MyAppName} · {#MyAppPublisher}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"; Comment: "卸载 {#MyAppName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "{#MyAppName} · {#MyAppPublisher}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 卸载前先结束仍在运行的程序，避免 DLL 被占用导致残留
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillRunningApp"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{localappdata}\InterviewReviewer"
