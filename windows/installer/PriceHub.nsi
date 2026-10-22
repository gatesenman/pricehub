; PriceHub Windows 安装程序（NSIS，中文界面）。自带 Python 3.11 嵌入版、核心依赖与 pywebview，安装后以独立桌面窗口启动，无需预装 Python、无需联网。
Unicode true
SetCompressor /SOLID lzma
SetCompressorDictSize 64
!include "MUI2.nsh"
!ifndef VERSION
  !define VERSION "1.13.0"
!endif
!ifndef STAGE
  !define STAGE "stage"
!endif
Name "PriceHub"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\PriceHub"
InstallDirRegKey HKCU "Software\PriceHub" "InstallDir"
RequestExecutionLevel user
BrandingText "PriceHub ${VERSION}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchPriceHub
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 PriceHub"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "ProductName" "PriceHub"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "FileDescription" "PriceHub 价格管理工作台 安装程序"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "LegalCopyright" "PriceHub"

Function LaunchPriceHub
  Exec '"$INSTDIR\python\pythonw.exe" "$INSTDIR\PriceHub.pyw"'
FunctionEnd

Section "PriceHub" SecMain
  SectionIn RO
  ; 升级：先停止正在运行的旧版本（用户数据在 %APPDATA%\PriceHub，不受影响）
  IfFileExists "$INSTDIR\python\pythonw.exe" 0 +3
    ExecWait '"$INSTDIR\python\pythonw.exe" "$INSTDIR\PriceHub.pyw" --stop'
    Sleep 1500
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  SetOutPath "$INSTDIR"
  File /r "${STAGE}\*.*"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  CreateDirectory "$SMPROGRAMS\PriceHub"
  CreateShortcut "$SMPROGRAMS\PriceHub\PriceHub.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\PriceHub.pyw"' "$INSTDIR\PriceHub.ico" 0
  CreateShortcut "$SMPROGRAMS\PriceHub\停止 PriceHub.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\PriceHub.pyw" --stop' "$INSTDIR\PriceHub.ico" 0
  CreateShortcut "$SMPROGRAMS\PriceHub\使用说明.lnk" "$INSTDIR\使用说明.txt"
  CreateShortcut "$SMPROGRAMS\PriceHub\卸载 PriceHub.lnk" "$INSTDIR\uninstall.exe"
  CreateShortcut "$DESKTOP\PriceHub.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\PriceHub.pyw"' "$INSTDIR\PriceHub.ico" 0
  WriteRegStr HKCU "Software\PriceHub" "InstallDir" "$INSTDIR"
  !define UK "Software\Microsoft\Windows\CurrentVersion\Uninstall\PriceHub"
  WriteRegStr HKCU "${UK}" "DisplayName" "PriceHub 价格管理工作台"
  WriteRegStr HKCU "${UK}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UK}" "Publisher" "PriceHub"
  WriteRegStr HKCU "${UK}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UK}" "DisplayIcon" "$INSTDIR\PriceHub.ico"
  WriteRegStr HKCU "${UK}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UK}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD HKCU "${UK}" "NoModify" 1
  WriteRegDWORD HKCU "${UK}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ExecWait '"$INSTDIR\python\pythonw.exe" "$INSTDIR\PriceHub.pyw" --stop'
  Sleep 1500
  Delete "$DESKTOP\PriceHub.lnk"
  RMDir /r "$SMPROGRAMS\PriceHub"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PriceHub"
  DeleteRegKey HKCU "Software\PriceHub"
  ; 用户数据 %APPDATA%\PriceHub 保留（价格历史、设置），如需彻底删除请手动删除该文件夹
SectionEnd
