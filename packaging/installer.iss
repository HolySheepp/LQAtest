; LQA Checker 安裝精靈
;
; 裝的是 PyInstaller 產出的整個資料夾。給非工程師的同事用，所以：
;   - 預設裝在使用者自己的 AppData，不需要系統管理員權限
;   - 介面用繁體中文
;   - 解除安裝時不刪 config / projects / logs，那是使用者的資料
;
; 產生安裝檔請跑 tools/build_installer.py，不要直接呼叫 ISCC —— 那個
; 腳本會先確認 dist 裡的東西是新的。

#define AppName "LQA Checker"
; 版本由 tools/build_installer.py 用 /DAppVersion= 帶進來，
; 這裡只是沒帶的時候的退路
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppPublisher "Cela"
#define AppExe "LQA Checker.exe"

[Setup]
AppId={{8E2F1C34-7B5A-4D6E-9A21-3F5C8D0B4E77}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 裝在使用者目錄，不必管理員權限 —— 公司電腦不一定給得起
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=LQA-Checker-{#AppVersion}-setup
VersionInfoVersion={#AppVersion}
SetupIconFile=..\lqa\gui\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 這包有三百多 MB，壓縮要花點時間但換得到小一半的檔案
DirExistsWarning=no
CloseApplications=yes

[Languages]
Name: "chinesetraditional"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "捷徑:"

[Files]
; 程式本體：每次安裝都覆蓋
Source: "..\dist\LQA Checker\*"; DestDir: "{app}";     Excludes: "config\*,self-test.txt";     Flags: ignoreversion recursesubdirs createallsubdirs
; 設定另外處理：已經有就不要蓋掉，移除時也不要刪 ——
; 那是使用者自己校準出來的參數，重裝一次就沒了的話等於要重來
Source: "..\dist\LQA Checker\config\*"; DestDir: "{app}\config";     Flags: onlyifdoesntexist uninsneveruninstall recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\檢查安裝是否完整"; Filename: "{app}\{#AppExe}"; Parameters: "--self-test"
Name: "{group}\移除 {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即開啟 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 程式本體拿掉，config / projects / logs 留著 ——
; 那是使用者的校準參數與工作成果，不該被解除安裝帶走
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\self-test.txt"
