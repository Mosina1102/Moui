; ============================================================================
; 墨忆MOUI-MAX · E2 Inno Setup 安装包脚本（exe化策划书 v2.4 §四）
; ----------------------------------------------------------------------------
; 设计铁律对照：
;   · per-user 免管理员（坑①/四审：UAC 弹窗对小白就是恐吓）→ PrivilegesRequired=lowest
;   · 升级保留区永不进 [Files]/[InstallDelete]（坑①⑦，热更新生命线）：
;       MaiBot\.env  MaiBot\config\  MaiBot\data\  MaiBot\mongodb\db|logs\
;       MaiBot\napcat\versions\*\resources\app\napcat\config\
;       MaiBot\MaiBot-Napcat-Adapter\config.toml
;   · 构建机实例配置绝不进分发物（三审①/五审①）：config 双角色文件以
;     .build\factory\ 出厂副本身份 onlyifdoesntexist 进包
;   · 前置杀进程（坑⑨+三审④）：按 pids.json PID 或完整路径杀 runtime python，
;     严禁按 python.exe 镜像名杀（防误杀用户自己的 Python）
;   · 卸载默认保留升级保留区（她的记忆），勾"彻底删除"才清（E2 第 7 条）
;   · 红线（E0 ③）：绝不触碰 Documents\Tencent Files（登录缓存落用户目录，
;     bot 与用户自己的 QQ 混在同一父目录，卸载器永远不碰）
; 版本注入：构建 bat 从仓库根 VERSION 生成 installer\version.iss（四审③）
; ============================================================================

; ---- 版本号由构建脚本生成（勿手改本文件的版本内容） ----
#include "version.iss"

#define MyAppName "墨忆MOUI-MAX"
#define MyAppPublisher "Mosina1102"
#define MyAppExeName "launcher.exe"
; AppId 固定写死：升级靠它定位旧安装（E2 第 1 条，永不改动）
#define MyAppId "{{B3F9C2A1-5D4E-4F86-9A7B-1C2D3E4F5A6B}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Moui
DefaultGroupName={#MyAppName}
; per-user 安装（免 UAC，E2 第 2 条）
PrivilegesRequired=lowest
; LZMA2 高压缩（策划书 §二/§八：Setup 约 600~800MB）
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
DirExistsWarning=no
SetupLogging=yes
OutputDir=..\dist
OutputBaseFilename=MoUIMax_Setup_{#MyAppVersion}_x64
UninstallDisplayName={#MyAppName}
; 打包图标（E3-4 出 logo.ico 后自动启用）
#if FileExists(SourcePath + "\moui_logo.ico")
SetupIconFile=moui_logo.ico
#endif
; 杀进程我们自己在 [Code] 里做（/CLOSEAPPLICATIONS 对无窗口服务进程不可靠，三审④）
CloseApplications=no
RestartApplications=no

[Languages]
; 简中语言文件随脚本分发（installer\ChineseSimplified.isl，取自 issrc is-6_7_4 官方 tag）
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Files]
; ---- 版本文件（E4 更新比较的单一事实源） ----
Source: "..\VERSION"; DestDir: "{app}"; DestName: "VERSION"; Flags: ignoreversion

; ---- 内嵌 Python 便携运行时（E1 产物） ----
Source: "..\dist\runtime\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs createallsubdirs ignoreversion

; ---- 启动器（E3 产物；首版打包前可能不存在，skipifsourcedoesntexist 占位） ----
Source: "..\dist\Launcher\*"; DestDir: "{app}\Launcher"; Flags: recursesubdirs createallsubdirs ignoreversion skipifsourcedoesntexist

; ---- MaiBot 源码根文件（旧 bat 退役，一个不带） ----
Source: "..\MaiBot\bot.py"; DestDir: "{app}\MaiBot"; Flags: ignoreversion
Source: "..\MaiBot\config_manager.py"; DestDir: "{app}\MaiBot"; Flags: ignoreversion
Source: "..\MaiBot\init_napcat.py"; DestDir: "{app}\MaiBot"; Flags: ignoreversion
Source: "..\MaiBot\pyproject.toml"; DestDir: "{app}\MaiBot"; Flags: ignoreversion
Source: "..\MaiBot\requirements.txt"; DestDir: "{app}\MaiBot"; Flags: ignoreversion
Source: "..\MaiBot\README.md"; DestDir: "{app}\MaiBot"; Flags: ignoreversion
Source: "..\MaiBot\LICENSE"; DestDir: "{app}\MaiBot"; Flags: ignoreversion
Source: "..\MaiBot\EULA.md"; DestDir: "{app}\MaiBot"; Flags: ignoreversion
Source: "..\MaiBot\PRIVACY.md"; DestDir: "{app}\MaiBot"; Flags: ignoreversion
Source: "..\MaiBot\Moui介绍与对比.md"; DestDir: "{app}\MaiBot"; Flags: ignoreversion
Source: "..\MaiBot\Moui白皮书.md"; DestDir: "{app}\MaiBot"; Flags: ignoreversion
Source: "..\MaiBot\data_migration_tutorial.txt"; DestDir: "{app}\MaiBot"; Flags: ignoreversion skipifsourcedoesntexist

; ---- 源码/资源目录 ----
Source: "..\MaiBot\src\*"; DestDir: "{app}\MaiBot\src"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\MaiBot\template\*"; DestDir: "{app}\MaiBot\template"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\MaiBot\depends-data\*"; DestDir: "{app}\MaiBot\depends-data"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\MaiBot\changelogs\*"; DestDir: "{app}\MaiBot\changelogs"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\MaiBot\scripts\*"; DestDir: "{app}\MaiBot\scripts"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\MaiBot\tests\*"; DestDir: "{app}\MaiBot\tests"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs createallsubdirs ignoreversion

; ---- 适配器（三审①：config.toml 绝不进包——开发者实例配置混入=所有用户拿到开发者的配置；
;     E0 打包黑名单：src.zip 等开发期压缩包一律剔除） ----
Source: "..\MaiBot\MaiBot-Napcat-Adapter\*"; DestDir: "{app}\MaiBot\MaiBot-Napcat-Adapter"; Excludes: "config.toml,__pycache__,*.pyc,logs,logs\*,venv,venv\*,.git,.git\*,*.zip"; Flags: recursesubdirs createallsubdirs ignoreversion

; ---- MongoDB 绿色版（bin 进包；db/logs 是升级保留区，坑⑦：实际目录名是 db 不是 data） ----
Source: "..\MaiBot\mongodb\*"; DestDir: "{app}\MaiBot\mongodb"; Excludes: "db,db\*,logs,logs\*"; Flags: recursesubdirs createallsubdirs ignoreversion

; ---- Compass 数据库编辑器（E0 收编第 11 项依赖） ----
Source: "..\MaiBot\MongoDBCompassPortable\*"; DestDir: "{app}\MaiBot\MongoDBCompassPortable"; Flags: recursesubdirs createallsubdirs ignoreversion

; ---- NapCat + QQ 本体（napcat/QQ 的一切 config 目录都是实例态=保留区，任意深度排除；
;     QQ 首启会自建自己的默认 config；坑③：登录缓存落 Documents\Tencent Files 包外天然保留） ----
Source: "..\MaiBot\napcat\*"; DestDir: "{app}\MaiBot\napcat"; Excludes: "config,config\*,logs,logs\*"; Flags: recursesubdirs createallsubdirs ignoreversion

; ---- onepackdata（vc_redist + WebView2 Evergreen offline，E2 第 5 条） ----
Source: "..\MaiBot\onepackdata\*"; DestDir: "{app}\MaiBot\onepackdata"; Flags: recursesubdirs createallsubdirs ignoreversion

; ---- 出厂配置副本（五审①裁决：template 生成的干净出厂件，onlyifdoesntexist=
;      首装给默认、升级永不覆盖实例；uninsneveruninstall=卸载不删用户配置） ----
Source: "..\.build\factory\bot_config.toml"; DestDir: "{app}\MaiBot\config"; DestName: "bot_config.toml"; Flags: onlyifdoesntexist uninsneveruninstall ignoreversion
Source: "..\.build\factory\lpmm_config.toml"; DestDir: "{app}\MaiBot\config"; DestName: "lpmm_config.toml"; Flags: onlyifdoesntexist uninsneveruninstall ignoreversion
Source: "..\.build\factory\.env"; DestDir: "{app}\MaiBot"; DestName: ".env"; Flags: onlyifdoesntexist uninsneveruninstall ignoreversion
Source: "..\.build\factory\adapter_config.toml"; DestDir: "{app}\MaiBot\MaiBot-Napcat-Adapter"; DestName: "config.toml"; Flags: onlyifdoesntexist uninsneveruninstall ignoreversion

[Dirs]
; 升级保留区目录建空壳（升级时天然保留；卸载默认不动）
Name: "{app}\MaiBot\config"; Flags: uninsneveruninstall
Name: "{app}\MaiBot\data"; Flags: uninsneveruninstall
Name: "{app}\MaiBot\mongodb\db"; Flags: uninsneveruninstall
Name: "{app}\MaiBot\mongodb\logs"; Flags: uninsneveruninstall

[InstallDelete]
; 白名单式清理旧程序区（防残留旧 .pyc 与新源码混跑，E2 第 4 条）；
; 升级保留区一个都不许出现（config/data/mongodb db|logs/napcat config/.env/adapter config.toml）
Type: filesandordirs; Name: "{app}\MaiBot\src"
Type: filesandordirs; Name: "{app}\MaiBot\template"
Type: filesandordirs; Name: "{app}\MaiBot\depends-data"
Type: filesandordirs; Name: "{app}\MaiBot\changelogs"
Type: filesandordirs; Name: "{app}\MaiBot\scripts"
Type: filesandordirs; Name: "{app}\MaiBot\tests"
Type: filesandordirs; Name: "{app}\MaiBot\MaiBot-Napcat-Adapter\src"
Type: filesandordirs; Name: "{app}\MaiBot\MaiBot-Napcat-Adapter\template"
Type: filesandordirs; Name: "{app}\MaiBot\MaiBot-Napcat-Adapter\logs"
Type: filesandordirs; Name: "{app}\MaiBot\logs"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\MaiBot\mongodb\bin"

[Icons]
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\Launcher\{#MyAppExeName}"; WorkingDir: "{app}\Launcher"
Name: "{group}\{#MyAppName}"; Filename: "{app}\Launcher\{#MyAppExeName}"; WorkingDir: "{app}\Launcher"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; VC 运行库静默装（幂等，已装秒过，E2 第 5 条）
Filename: "{app}\MaiBot\onepackdata\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "正在安装系统运行库（VC++）…"; Flags: runhidden waituntilterminated
; WebView2 缺失才静默补装（幂等；面板/神经网窗口依赖，风险 7）
Filename: "{app}\MaiBot\onepackdata\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Parameters: "/silent /install"; StatusMsg: "正在安装 WebView2 运行时…"; Flags: runhidden waituntilterminated; Check: NeedWebView2
; 装完可选直接拉起启动器
Filename: "{app}\Launcher\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifdoesntexist; WorkingDir: "{app}\Launcher"

[UninstallDelete]
; 仅程序区兜底残留；升级保留区与用户数据一概不碰（E2 第 7 条默认卸载行为）
Type: filesandordirs; Name: "{app}\runtime"

[Code]
var
  PurgeData: Boolean;

// ---------- 进程防线（E2 第 6 条） ----------

procedure ExecHidden(const Cmd, Param: string);
var
  R: Integer;
begin
  Exec(Cmd, Param, '', SW_HIDE, ewWaitUntilTerminated, R);
end;

procedure KillByImage(const Image: string);
begin
  ExecHidden(ExpandConstant('{cmd}'), '/C taskkill /F /IM "' + Image + '" /T > nul 2>&1');
end;

procedure KillByPath(const ExePath: string);
var
  PS: string;
begin
  // 按完整路径杀（三审④：严禁按 python.exe 镜像名杀，防误杀用户自己的 Python）
  PS := 'Get-CimInstance Win32_Process -Filter "ExecutablePath = ''' + ExePath + '''" | ' +
        'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }';
  ExecHidden(ExpandConstant('{cmd}'), '/C powershell -NoProfile -ExecutionPolicy Bypass -Command "' + PS + '"');
end;

procedure KillByPidsJson;
var
  F: string;
  Content: string;
  Ids: TArrayOfString;
  I: Integer;
begin
  // launcher 记录的 PID 精确杀（logs/launcher/pids.json）
  F := ExpandConstant('{app}\MaiBot\logs\launcher\pids.json');
  if not FileExists(F) then Exit;
  if LoadStringsFromFile(F, Ids) then
  begin
    Content := '';
    for I := 0 to GetArrayLength(Ids) - 1 do
      Content := Content + Ids[I];
    try
      ExecHidden(ExpandConstant('{cmd}'),
        '/C powershell -NoProfile -Command "[regex]::Matches(''' + Content + ''', ''"pid"\s*:\s*(\d+)'') | ForEach-Object { taskkill /F /T /PID $_.Groups[1].Value } > nul 2>&1"');
    except
      // fail-open：PID 清理失败还有后面的镜像名/路径杀兜底
    end;
  end;
end;

procedure KillByDirPrefix(const Image, DirPrefix: string);
var
  PS: string;
begin
  // 路径限定击杀：只杀 exe 位于 DirPrefix 之下的镜像（老大真机反馈 2026-10-30：
  // 无差别 taskkill QQ.exe 会弹掉用户自己的 QQ——我们的在包内 napcat 目录，按路径切开）
  PS := 'Get-CimInstance Win32_Process -Filter "Name=''' + Image + '''" | ' +
        'Where-Object { $_.ExecutablePath -like ''' + DirPrefix + '*'' } | ' +
        'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }';
  ExecHidden(ExpandConstant('{cmd}'), '/C powershell -NoProfile -ExecutionPolicy Bypass -Command "' + PS + '"');
end;

procedure KillAllProcesses;
begin
  KillByPidsJson;                                 // 1) PID 精确杀（最准）
  KillByImage('launcher.exe');                    // 2) launcher 只可能是我们的，镜像名即可
  KillByDirPrefix('NapCatWinBootMain.exe', ExpandConstant('{app}\MaiBot\napcat'));
  KillByDirPrefix('QQ.exe', ExpandConstant('{app}\MaiBot\napcat'));   // 只杀包内 QQ，不动用户自己的
  KillByDirPrefix('mongod.exe', ExpandConstant('{app}\MaiBot\mongodb\bin'));  // 只杀包内 mongod
  KillByPath(ExpandConstant('{app}\runtime\python.exe'));  // 3) 我们的 runtime python 按路径杀
end;

// 前置防线挂在 PrepareToInstall（点完"安装"后、动文件前；{app} 此刻已初始化——
// InitializeSetup 阶段 ExpandConstant('{app}') 会炸 "before it was initialized"，真机验证过）
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillAllProcesses;
  // 给自杀操作一点收尾时间（Windows 进程句柄释放延迟）
  Sleep(1500);
  NeedsRestart := False;
  Result := '';   // 空 = 继续安装
end;

function InitializeUninstall: Boolean;
begin
  KillAllProcesses;
  Sleep(1500);
  Result := True;
end;

// ---------- WebView2 检测（E2 第 5 条，v2.3） ----------

function NeedWebView2(): Boolean;
begin
  Result := not (
    RegKeyExists(HKEY_LOCAL_MACHINE, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') or
    RegKeyExists(HKEY_CURRENT_USER, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
  );
end;

// ---------- 卸载：默认保留她的记忆，勾"彻底删除"才清（E2 第 7 条） ----------

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    PurgeData := (MsgBox(
      '要彻底删除她的全部数据吗？' #13#10 #13#10 +
      '选择「是」：删除数据、数据库与全部配置（她的记忆将无法找回）' #13#10 +
      '选择「否」：仅卸载程序，她的记忆保留在 MaiBot 内，重装即可找回。',
      mbConfirmation, MB_YESNO) = IDYES);
    if not PurgeData then
      MsgBox('她的记忆已保留在 MaiBot 文件夹内，重新安装即可找回。', mbInformation, MB_OK);
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    if PurgeData then
      DelTree(ExpandConstant('{app}'), True, True, True);
    // 红线（E0 ③）：无论哪种选择，绝不触碰 Documents\Tencent Files（用户自己的 QQ 也住在那）
  end;
end;
