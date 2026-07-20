# 開發計畫(依序執行,每步有驗收條件)

規格見 [DESIGN.md](DESIGN.md),環境與 ground truth 見 [CLAUDE.md](CLAUDE.md)。
每步流程:實作 → 跑該步驗收命令 → 全綠 → git commit → 下一步。
驗收條件寫的數字全部來自 CLAUDE.md 的 ground truth 表,不要放寬容忍值矇混過關;
若實測與 ground truth 有出入,先人工檢查是 parser 錯還是 ground truth 標註粒度問題,把結論寫進 commit message。

---

## Step 1:專案骨架
- `git init`;建立 pyproject.toml(package `wbsgen`,console_script `wbs`,deps:pymupdf、pdfplumber、typer、pydantic、rich、openpyxl、openai;dev extras:pytest)。
- `src/wbsgen/`:cli.py(typer app,先實作 `init`/`ingest`/`status`/`run` 空殼)、manifest.py(狀態機:15 stage 的 status/input_hash/時間/錯誤,JSON 落地)、models.py(DESIGN.md 第四節列出的全部 Pydantic 模型)。
- `wbs init` 建 wbs.toml(內容照 DESIGN.md 第五節,`[llm]` 加 `mock = true`)與 data/ 骨架,冪等。
- `wbs ingest` 複製 PDF 至 original/(設唯讀屬性)、SHA-256、建 manifest、印 project id。

**驗收**:`wbs init && wbs ingest tests\golden\contract_11108.pdf --project gold` 後 `wbs status gold` 顯示 15 個 stage 均為 PENDING;重複執行 ingest 不報錯(冪等)。

## Step 2:s01 parse + s02 quality
- s01:PyMuPDF 逐頁抽 block(bbox/字型/字級/bold)、字型資源(含 ToUnicode 有無)、圖片數;pdfplumber 抽單頁表格碎片(先存 raw,不合併)。產物:`01_parse/pages/*.json`、`blocks.jsonl`、`fonts.json`。
- s02:每頁計算 char_count、cjk_ratio、**common_han_ratio**(內建常用中文字集,可用 Big5 常用字範圍生成)、symbol_ratio、font ToUnicode 有效比例;分類 NORMAL_TEXT/IMAGE_ONLY/GARBLED_TEXT/MIXED/EMPTY。判定邏輯照 DESIGN.md/CLAUDE.md:主訊號 = 常用中文字覆蓋率 + ToUnicode,alnum 只當輔助。
- 建 `tests/golden/expected.json`(從 CLAUDE.md ground truth 表轉成 JSON)與 `tests/golden/test_golden.py`(逐項比對,尚未實作的項目標 skip)。

**驗收**:`python -m pytest tests/golden -x -q` 通過:總頁數 309;NORMAL=164、IMAGE_ONLY=10(p154–163)、GARBLED=135(p175–309)。±0 容忍——若差 1–2 頁,逐頁檢查該頁實際內容再決定修 parser 或修 expected(需在 commit message 說明)。

## Step 3:s03 layout + s04 subdoc
- s03:全域(不分子文件)對頁面頂部/底部 12% 的 block 做正規化(剝數字→`{n}`、去空白),以「同 y 座標帶 + 正規化後相同 + 連續 ≥2 頁」判 running header/footer family。輸出 families.json;對應 block 標 `role` 與兩個 exclude 旗標,不刪除。
- s04:以 family 變化點、印刷頁碼重起、封面標題、字型分布變化等多訊號評分切子文件;類型分類照 DESIGN.md。

**驗收**:goldtest 新增項目通過:(a) 需求規範書章名頁首(如「肆、維運服務」)被標為 RUNNING_HEADER 且 p72 正文的「壹、專案目標」標題 block 未被誤標;(b) 子文件邊界含 65|66、93|94、126|127、153|154、163|164、174|175 這六個切點;(c) 需求規範書 subdoc 為 p66–93、type=REQUIREMENT_SPECIFICATION。

## Step 4:s05 toc + s06 section
- s05:在需求規範書內找目次頁(p68),解析章名+印刷頁碼;契約本文目錄(p2–3)同理。
- s06:目錄項正規化後到正文 fuzzy match 定位標題 block(結合字級/bold/位置/非 running header),建 Section(start/end anchor 到 block 級)。目錄頁碼只當候選,以正文定位為準。
- `wbs inspect <id> sections` 實作。

**驗收**:goldtest:需求規範書切出恰好 7 章(壹~柒);sec-001(專案目標)與 sec-002(現況概述)的 start_anchor 同在 p72 但 block 不同;每章 start_anchor 指向正文標題而非頁首。

## Step 5:s07 table + s08 assemble
- s07:表格碎片 → column signature(正規化表頭)→ 續表判定(簽名相同+無新 caption+位置訊號)→ 合併去重複表頭;跨頁同列拼接(首欄空+上頁未完句 → 併列,信心 < merge_confidence_min 標 NEEDS_REVIEW)。caption 從表格上方最近 text block 取「表N …」。
- s08:每章依閱讀順序組裝正文 block + 表格 Markdown 成 `08_assemble/sec-XXX.md`(即未來 LLM input,人可目視檢查)。

**驗收**:goldtest:辨識出表1~表10 共 10 張(caption 比對);表5 page_start=81、page_end=85、合併後為單一 table 且 rows 無重複表頭列;`08_assemble/` 的維運服務章 md 內含表3 與表5 的 Markdown 且位於正確的正文段落之間。

## Step 6:LLM client + s09~s13
- `llm/client.py`:OpenAI-compatible,`generate_json(system, user, schema)`:回應過 jsonschema 驗證,失敗帶錯誤 retry(max_retries),全滅則 stage 失敗落地錯誤。`mock=true` 時走 MockClient:依 schema 回傳合成資料(表格列→一個 work item 的規則),存取皆記 llm_logs。
- prompts/ 六個模板照 DESIGN.md;s09 規則分類(關鍵詞+表格 caption)+ Task A 確認;s10 逐章/逐表抽取(Task B);s11 關係(Task C);s12 局部 WBS(Task D);s13 全域合併+編碼(Task E),MANUAL/locked 節點保留。
- `wbs inspect <id> items|wbs|trace` 實作。

**驗收**:mock 模式下 `wbs run gold --from classify --to merge` 跑通;`13_merge/wbs.json` 通過 Pydantic;每個 EXPLICIT 節點的 source 鏈可經 `wbs inspect gold trace --id <node>` 回溯到 block/表格列與 PDF 頁碼;亂碼頁與掃描頁未出現在任何 llm_logs 的 input 中(寫成 unit test)。

## Step 7:s14 validate + s15 export + auto
- s14:DESIGN.md 第十八節四類檢查(結構/語意/來源/覆蓋),輸出 report.json 與 NEEDS_REVIEW 彙總。
- s15:openpyxl 匯出 Excel(WBS 階層縮排 + code + 來源頁碼 + explicit/inferred 欄)、wbs.json、Mermaid(mindmap 或 graph)、csv、coverage.txt(照 DESIGN.md「畫面可顯示」那組數字)。
- `wbs auto`、`wbs rerun`、`wbs export`、`wbs goldtest` 子命令補完;exit code 0/2/3 規則落實。

**驗收**:`wbs auto tests\golden\contract_11108.pdf` 一條命令跑完,exit code=2(有亂碼區段告警屬預期),exports/ 產出 4 種格式,coverage.txt 顯示 309/164/10/135/9 子文件/7 章/10 表;`python -m pytest tests -x -q` 全綠;`wbs goldtest` 全項通過。

---

## 完成定義
全部 7 步 commit 完成、`wbs goldtest` 無 skip 全綠、README.md(簡短:安裝+三條常用命令)存在。
之後的 Phase 2(真 LLM 端點接入、OCR)不在本計畫範圍,不要擅自開工。
