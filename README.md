# rbxl-to-folder

把 Roblox 的 place / model 檔解開成 **AI 容易閱讀的資料夾檔案樹**(以腳本原始碼為主 + 完整階層大綱)。

支援輸入:`.rbxl`、`.rbxlx`、`.rbxm`、`.rbxmx`。

## 用法

```bash
python extract.py <檔案> [-o 輸出目錄] [--engine auto|rust|python]
```

範例:

```bash
python extract.py MyGame.rbxl
python extract.py MyGame.rbxl -o ./MyGame_dump
python extract.py Model.rbxmx --engine python
```

不指定 `-o` 時,輸出到 `<檔名>_extracted/`。

## 輸出內容

```
<輸出>/
  README.md       # 給 AI 的版面說明
  tree.txt        # 完整階層,像 Studio Explorer 的樹狀(├──/└── 樹枝 + [ClassName])
  manifest.json   # 機器可讀:每個 instance 的 path / class / 是否腳本 / 對應檔
  src/            # Rojo 風格鏡射,只在「含腳本」的路徑上建資料夾
```

命名慣例(同 Rojo):

| 類別 | 檔名 |
|------|------|
| `Script` | `*.server.lua` |
| `LocalScript` | `*.client.lua` |
| `ModuleScript` | `*.lua` |

有(含腳本的)子物件的腳本 → 轉成資料夾,本體放 `init.server.lua` / `init.client.lua` / `init.lua`。
非腳本且底下沒有腳本的物件不會建空資料夾,只列在 `tree.txt`。

## 兩個引擎

1. **Rust 主引擎(`rust/`)** — 用官方等級的 [rbx-dom](https://github.com/rojo-rbx/rbx-dom)
   crates(`rbx_binary` / `rbx_xml` / `rbx_dom_weak`)。全格式、全屬性高保真。
   首次執行時 `extract.py` 會自動 `cargo build --release`。需要安裝 Rust(`cargo`)。
2. **Python fallback** — 當 `cargo` 不在、建置或執行失敗時自動啟用。
   - XML(`.rbxlx`/`.rbxmx`):標準庫 `xml.etree.ElementTree`,完整。
   - 二進位(`.rbxl`/`.rbxm`):自寫精簡解析,只擷取階層與腳本 `Source`。
     需要套件:`pip install lz4 zstandard`。

強制走 Python:`--engine python` 或設環境變數 `RBXL_FORCE_PYTHON=1`。

## 設計取捨

Python fallback 的二進位解析**刻意只支援腳本擷取所需的部分**(`INST`/`PROP`/`PRNT`
chunk,且 `PROP` 只解 String 型別的 `Name`/`Source`),不解 CFrame/Color3 等幾何
型別。需要完整屬性保真時請用 Rust 引擎(預設就是)。
