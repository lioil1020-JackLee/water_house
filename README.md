# Water House SCADA System

一個基於 PyQt6 和 OPC UA 的飯店保全壓扣監控系統，提供即時警報監控和重置功能。

## 功能特點

### 核心功能
- **即時監控**: 通過 OPC UA 協議連接到保全控制系統，實時讀取壓扣狀態和警報數據
- **警報管理**: 監控各種保全壓扣警報系統，包括：
  - 公共澡堂壓扣警報系統
  - 殘障廁所警報系統
  - 客房警報系統
- **警報重置**: 支持遠程重置警報狀態
- **數據寫入**: 支持遠程控制和參數調整
- **用戶介面**: 直觀的圖形化介面，支持觸控操作

### 技術特點
- **跨平台支援**: 基於 PyQt6，支持 Windows、Linux 和 macOS
- **主題適配**: 自動檢測系統主題（淺色/深色模式），提供舒適的視覺體驗
- **音效提示**: 集成音效檔案，提供操作和警報音效反饋
- **數字鍵盤**: 專用數字輸入介面，適合觸控螢幕操作
- **系統托盤**: 最小化到系統托盤，保持後台運行

## 系統需求

- **Python**: >= 3.12
- **作業系統**: Windows 10/11, Linux, macOS
- **記憶體**: 至少 512MB RAM
- **儲存空間**: 至少 100MB 可用空間

## 安裝說明

### 1. 環境準備
確保系統已安裝 Python 3.12 或更高版本：

```bash
python --version
```

### 2. 安裝依賴項
使用 pip 安裝所需套件：

```bash
pip install -r requirements.txt
```

或使用 uv（推薦）：

```bash
uv pip install -r requirements.txt
```

### 3. 運行應用程式
直接運行主程式：

```bash
python water_house.py
```

## 建置說明

### 使用 PyInstaller 打包
專案提供兩個打包配置：

#### OneDir 模式（推薦）
將應用程式打包到單一目錄：

```bash
pyinstaller water_house_onedir.spec
```

#### OneFile 模式
將應用程式打包成單一可執行檔案：

```bash
pyinstaller water_house_onefile.spec
```

打包後的可執行檔案位於 `build/` 目錄中。

## 專案結構

```
water_house/
├── water_house.py          # 主程式入口
├── ui/                     # 用戶介面模組
│   ├── scada_dialog.py     # 主 SCADA 介面
│   └── popup_dialog.py     # 彈出對話框（數字鍵盤等）
├── scada/                  # SCADA 相關資源
├── img/                    # 圖片和音效資源
├── OPC UA tag.csv          # OPC UA 標籤定義
├── requirements.txt        # Python 依賴項
├── pyproject.toml          # 專案配置
└── README.md              # 專案說明
```

## 配置說明

### OPC UA 連接
應用程式會自動連接到預設的 OPC UA 伺服器。標籤定義位於 `OPC UA tag.csv` 檔案中，包含：

- 標籤名稱
- OPC UA NodeId
- 數據類型
- 存取權限

### 主題設定
應用程式會自動檢測 Windows 系統主題偏好設定。如需手動調整，請修改 `water_house.py` 中的主題邏輯。

## 使用說明

1. **啟動應用程式**: 運行 `water_house.py` 或打包後的可執行檔案
2. **連接監控**: 應用程式會自動嘗試連接到 OPC UA 伺服器
3. **監控數據**: 在主介面查看實時數據和警報狀態
4. **控制操作**: 使用介面按鈕進行遠程控制
5. **參數調整**: 點擊數值欄位使用數字鍵盤輸入新值

## 故障排除

### 常見問題

**連接失敗**
- 檢查 OPC UA 伺服器是否運行
- 確認網路連接正常
- 查看防火牆設定

**主題顯示異常**
- 確保系統支援主題檢測
- 手動調整 `water_house.py` 中的 `is_light` 變數

**打包後無法運行**
- 確保所有依賴項正確安裝
- 檢查 PyInstaller 版本相容性
- 確認資源檔案路徑正確

## 開發資訊

### 技術棧
- **GUI 框架**: PyQt6
- **通訊協議**: OPC UA (asyncua 庫)
- **打包工具**: PyInstaller
- **程式語言**: Python 3.12+

### 開發環境設定
1. 安裝開發依賴項：
   ```bash
   pip install -e .
   ```

2. 安裝開發工具：
   ```bash
   pip install black flake8 mypy
   ```

### 程式碼風格
專案遵循 PEP 8 程式碼風格標準。使用 Black 進行程式碼格式化。

## 授權

本專案採用 MIT 授權條款。詳見 LICENSE 檔案。

## 貢獻

歡迎提交 Issue 和 Pull Request！請確保：

1. 程式碼通過所有測試
2. 遵循現有程式碼風格
3. 更新相關文檔

## 版本歷史

### v0.1.0
- 初始版本
- 基本 SCADA 監控功能
- OPC UA 通訊支援
- PyQt6 GUI 介面
- 自動主題適配

## 聯絡資訊

如有問題或建議，請通過以下方式聯絡：
- 提交 GitHub Issue
- 發送郵件至專案維護者

---

**注意**: 本應用程式專為特定工業控制系統設計，請在專業指導下使用。