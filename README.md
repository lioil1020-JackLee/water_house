# Water House SCADA System

以 PyQt6 與 OPC UA 為基礎的桌面 SCADA 應用程式，用於監看水處理相關設備狀態、顯示警報與提供視覺化操作介面。

## 專案分析

目前專案是一個單一入口的桌面應用：

- `water_house.py` 是主程式入口，負責啟動 Qt 應用、載入對話框模組，以及設定亮暗色系配色。
- `ui/` 放置主要 UI 邏輯，`scada_dialog.py` 與 `popup_dialog.py` 構成主要互動畫面。
- `img/` 放置執行與打包時需要的圖片、音效與 icon 資源。
- `OPC UA tag.csv` 為 OPC UA 標籤資料來源。
- `water_house_onedir.spec` 與 `water_house_onefile.spec` 為 PyInstaller 打包設定。
- `.github/workflows/build-release.yml` 負責 Windows 與 macOS 的建置與發版。

## 依賴管理

本專案現在統一由 `uv` 管理：

- 主要執行依賴定義在 `pyproject.toml`
- 建置依賴放在 `build` dependency group
- 開發工具放在 `dev` dependency group
- 鎖定版本記錄在 `uv.lock`

不再使用 `requirements.txt`。

## 環境需求

- Python 3.12 以上
- 已安裝 `uv`

## 安裝與執行

安裝依賴：

```bash
uv sync
```

啟動程式：

```bash
uv run python water_house.py
```

## 開發工具

安裝開發工具：

```bash
uv sync --group dev
```

執行格式化與檢查：

```bash
uv run black .
uv run flake8 .
uv run mypy .
```

## 打包

安裝建置依賴：

```bash
uv sync --group build
```

建立 OneDir 版本：

```bash
uv run python ops/build-app.py onedir
```

建立 OneFile 版本：

```bash
uv run python ops/build-app.py onefile
```

產物會輸出到 `dist/`。

Windows 請使用上述建置入口；它會限制 DLL 搜尋路徑，避免系統其他工具的 ICU／Qt 函式庫被誤打包。CI 會直接執行打包後的 EXE 做離線自測。

## 客戶端常駐與故障追蹤

目前版本為 **v4.2.0**。「警報紀錄」隨時可開啟本機歷史表格，確認後仍保留紀錄；健康文字的提示可查看 OPC UA 要求與伺服器實際核定間隔。更新、操作差異及四台現場驗收步驟見 [DEPLOYMENT.md](DEPLOYMENT.md)。Windows Release 的 onefile／onedir 壓縮檔均包含主程式、新版 watchdog 與圖示，請一起更新。

Windows onefile 版本會將執行記錄寫入：

```text
%LOCALAPPDATA%\WaterHouse\logs\
```

其中 `water_house.log` 記錄一般啟動與 OPC UA 錯誤，`alarm_events.jsonl` 保存警報接收、清除、確認及顯示紀錄，`water_house_fault.log` 記錄底層例外。每 PID 的 `water_house.<PID>.heartbeat` 供 watchdog 判斷 GUI 心跳與監控執行緒；共用 `water_house.heartbeat` 保留供人工診斷。

將 `water_house_watchdog.exe` 與 `water_house.exe` 放在同一資料夾並啟動 watchdog。它每 5 秒檢查相同 EXE 路徑及 Windows 工作階段，處理程序消失、GUI 心跳停滯及監控執行緒停止。網路中斷由 OPC worker 重連。watchdog 記錄位於上述 logs 資料夾。原始碼 `water_house_watchdog.ahk` 已納入版本控制，`ops/build-watchdog.ps1` 使用固定版本及 SHA256 校驗的官方工具編譯。

執行離線測試（不連線現場 OPC）：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
uv run python -m unittest discover -s tests -v
uv run python water_house.py --self-test-output build/source-smoke.json
```

## 專案結構

```text
water_house/
├── .github/workflows/build-release.yml
├── img/
├── ui/
├── OPC UA tag.csv
├── pyproject.toml
├── README.md
├── uv.lock
├── water_house.py
├── water_house_onedir.spec
└── water_house_onefile.spec
```

## CI/CD

GitHub Actions 使用 Python 3.12、`uv` 與支援 Node.js 24 的 Actions：

- 推送 `main` 或向 `main` 送出 PR 時，在 Windows 與 macOS 執行離線測試。
- 推送 `v*` 版本標籤時，才建置 Windows／macOS 的 onedir、onefile，執行打包產物自測，再發布 Release。
- 手動執行 workflow 會完成四種打包及自測並保留 Artifacts，不發布或修改 Release。
- 同步推送主分支與版本標籤時，不會重複執行完整打包，也不會自動取消既有執行。
- 依賴由 `uv sync --locked --group build` 同步；打包使用 `ops/build-app.py`，Windows 另驗證 watchdog 編譯與圖示。

## 授權

本專案採用 MIT License，詳見 `LICENSE`。
