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
uv run pyinstaller water_house_onedir.spec
```

建立 OneFile 版本：

```bash
uv run pyinstaller water_house_onefile.spec
```

產物會輸出到 `dist/`。

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

GitHub Actions 已改為使用 `uv`：

- 安裝 `uv`
- 安裝 Python 3.12
- 使用 `uv sync --locked --group build` 同步依賴
- 使用 `uv run pyinstaller ...` 產生各平台打包產物

## 授權

本專案採用 MIT License，詳見 `LICENSE`。
