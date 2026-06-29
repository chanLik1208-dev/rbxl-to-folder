# rbxl-to-folder

**Repository:** <https://github.com/chanLik1208-dev/rbxl-to-folder>

Unpack a Roblox place / model file into an **AI-readable folder tree** — script
source code first, plus a full hierarchy outline.

Supported inputs: `.rbxl`, `.rbxlx`, `.rbxm`, `.rbxmx`.

## Usage

```bash
python extract.py <file> [-o OUTDIR] [--engine auto|rust|python]
```

Examples:

```bash
python extract.py MyGame.rbxl
python extract.py MyGame.rbxl -o ./MyGame_dump
python extract.py Model.rbxmx --engine python
```

Without `-o`, output goes to `<name>_extracted/`.

## Output

```
<out>/
  README.md       # layout notes for the AI
  tree.txt        # full hierarchy, Studio-Explorer style (├──/└── branches + [ClassName])
  manifest.json   # machine-readable: each instance's path / class / isScript / file
  src/            # Rojo-style mirror; folders only along script-bearing paths
```

Naming conventions (same as Rojo):

| Class | File |
|-------|------|
| `Script` | `*.server.lua` |
| `LocalScript` | `*.client.lua` |
| `ModuleScript` | `*.lua` |

A script that has (script-bearing) children becomes a folder, with its own code
in `init.server.lua` / `init.client.lua` / `init.lua`. Non-script instances with
no scripts below them are listed in `tree.txt` only (no empty folders).

## Two engines

1. **Rust engine (`rust/`)** — built on the reference-grade
   [rbx-dom](https://github.com/rojo-rbx/rbx-dom) crates
   (`rbx_binary` / `rbx_xml` / `rbx_dom_weak`). Full format and full-fidelity
   property support. `extract.py` runs `cargo build --release` automatically on
   first use. Requires Rust (`cargo`).
2. **Python fallback** — kicks in automatically when `cargo` is missing or the
   build/run fails.
   - XML (`.rbxlx` / `.rbxmx`): stdlib `xml.etree.ElementTree`, complete.
   - Binary (`.rbxl` / `.rbxm`): a focused hand-written parser that pulls only
     the hierarchy and script `Source`. Needs `pip install lz4 zstandard`.

Force Python: `--engine python` or set `RBXL_FORCE_PYTHON=1`.

## Design trade-off

The Python fallback's binary parser **intentionally supports only what script
extraction needs** (`INST` / `PROP` / `PRNT` chunks, and `PROP` only decodes
String-typed `Name` / `Source`) — it does not decode geometry types like
CFrame / Color3. For full property fidelity, use the Rust engine (the default).

---

# rbxl-to-folder(中文)

把 Roblox 的 place / model 檔解開成 **AI 容易閱讀的資料夾檔案樹**(以腳本原始碼為主 + 完整階層大綱)。

支援輸入:`.rbxl`、`.rbxlx`、`.rbxm`、`.rbxmx`。

## 用法

```bash
python extract.py <檔案> [-o 輸出目錄] [--engine auto|rust|python]
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

命名慣例(同 Rojo):`Script` → `*.server.lua`、`LocalScript` → `*.client.lua`、
`ModuleScript` → `*.lua`。有(含腳本的)子物件的腳本 → 轉成資料夾,本體放 `init.*`。
非腳本且底下沒有腳本的物件不會建空資料夾,只列在 `tree.txt`。

## 兩個引擎

1. **Rust 主引擎(`rust/`)** — 用官方等級的
   [rbx-dom](https://github.com/rojo-rbx/rbx-dom) crates,全格式、全屬性高保真;
   首次執行自動 `cargo build --release`,需要 Rust(`cargo`)。
2. **Python fallback** — `cargo` 不在或失敗時自動啟用。XML 用標準庫;二進位自寫精簡
   解析,只擷取階層與腳本 `Source`,需 `pip install lz4 zstandard`。
   強制走 Python:`--engine python` 或 `RBXL_FORCE_PYTHON=1`。

## 設計取捨

Python fallback 的二進位解析**刻意只支援腳本擷取所需的部分**(`INST`/`PROP`/`PRNT`,
且 `PROP` 只解 String 型別的 `Name`/`Source`),不解 CFrame/Color3 等幾何型別。需要
完整屬性保真時請用 Rust 引擎(預設就是)。
