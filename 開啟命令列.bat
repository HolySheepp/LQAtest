@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
echo.
echo   已在專案資料夾開啟命令列，可以直接輸入指令。
echo.
echo   常用：
echo     lqa speakers            產生發話者對照表範本
echo     lqa check-script        檢查翻譯文本讀不讀得到
echo     lqa windows             列出視窗標題
echo     lqa calibrate --window 雷電模擬器
echo     lqa probe               診斷校準結果
echo     lqa record --name smoke --max-lines 10
echo     lqa show-session sessions\最新的資料夾 --full
echo     lqa compare --session sessions\最新的資料夾 --out reports\smoke
echo.
echo   每個指令後面加 --help 可以看完整參數。
echo.
cmd /k
