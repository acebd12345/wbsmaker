# WBS 產生器(契約文件解析 + WBS 生成 CLI)

## 你的任務
依照 [DESIGN.md](DESIGN.md)(系統設計)與 [PLAN.md](PLAN.md)(開發步驟)實作 `wbs` CLI 工具。
DESIGN.md 是規格基線,不要偏離其資料模型與 stage 劃分;實作細節有疑義時以 PLAN.md 的驗收條件為準。

## 環境
- Windows 10 / PowerShell。Python 以 `py -3.12` 啟動;系統 Python 已有 pymupdf(fitz)、pdfplumber、pypdf,但一律在 `.venv` 內開發。
- 所有原始碼、JSON 產物、終端輸出一律 UTF-8。CLI 進入點需 `sys.stdout.reconfigure(encoding="utf-8")`,否則中文在 Windows console 會炸。
- 檔案路徑一律用 `pathlib`,不要手寫反斜線字串。

## 黃金測試契約(ground truth)
`tests/golden/contract_11108.pdf`(22MB,**唯讀,絕對不可修改或覆寫**)。
已人工驗證的事實,直接作為 `tests/golden/expected.json` 的依據:

| 項目 | 值 |
|---|---|
| 總頁數 | 309 |
| 正常文字頁 | 164 |
| 掃描頁(IMAGE_ONLY) | 10(PDF p154–163,零文字、每頁 1 張圖) |
| 亂碼頁(GARBLED_TEXT) | 135(PDF p175–309,字元數多但為子集字型錯誤字碼,如 `!"#$`、`$$$2$$3`;部分頁 alnum 比例不低,不可用 alnum 判斷) |
| 子文件 | 契約本文 p2–48 / 附件1–10 p49–64 / 簽署頁 p65 / 需求規範書 p66–93 / 投標須知 p94–126 / 評選須知 p127–147 / 招標公告 p148–153 / 掃描頁 p154–163 / 法規文件 p164–174 / 服務建議書(亂碼)p175–309 |
| 需求規範書章節 | 7 章(壹~柒);**壹與貳同在 PDF p72**(必須 block 級切割);目次印刷頁碼 + 71 = PDF 頁碼,且存在 off-by-one(伍:目次寫 16,實際 p86 印 15) |
| Running header | 需求規範書每頁頂部重複「當前章名 + 印刷頁碼」(如「肆、維運服務 9」),隨章節變動,只連續出現數頁 |
| 頁尾家族 | 投標須知「第 X 頁,共 33 頁投標須知」;評選須知「〔110/11/08〕」 |
| 表格 | 表1~表10;表5(維運服務需求)跨 PDF p81–85,續頁重複表頭「項次|項目|需求說明」 |
| 法規表格 | p164–168 為 `│├─` 框線字元純文字表,pdfplumber 偵測不到,屬正常(分類為 LAW_OR_POLICY 即可) |

## 開發守則
- **零互動**:任何命令不得出現 input()/確認提示。歧義寫入 NEEDS_REVIEW 繼續執行。
- **LLM 預設用 mock**:內部 Gemma 4 端點(wbs.toml `[llm]`)在開發機不可連。`llm/client.py` 必須支援 `mock: true` 模式(讀 `tests/fixtures/llm/*.json` 的罐頭回應或依 schema 生成假資料),s09–s13 的開發與測試全部在 mock 下完成。Stage s01–s08 完全不依賴 LLM。
- **每步收尾必跑驗證**:完成 PLAN.md 的一步後,執行該步的驗收命令(unit test + goldtest 對應項目),全綠才進下一步;失敗就修,不要帶病前進。
- 亂碼/掃描頁絕不送入 LLM input(含 mock)。
- 每個 stage 產物落地後用 Pydantic 驗證再寫檔;manifest 記 input hash 實現跳過與斷點續跑。
- Commit 習慣:每完成 PLAN.md 一步就 `git add -A && git commit`,訊息格式「step N: <內容>」。專案尚未 git init,第一步先 init。

## 常用命令
```powershell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest tests/unit -x -q          # 單元測試
python -m pytest tests/golden -x -q        # 黃金回歸(s01 起逐步啟用)
wbs auto tests\golden\contract_11108.pdf   # 全管線(LLM mock)
```
