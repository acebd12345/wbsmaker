# WBS 產生器 — Terminal 版系統設計

契約文件智慧解析與 WBS 生成系統的 CLI 實作設計。
本設計以 ChatGPT 最終架構報告(29 節版)為基線,將部署形態改為
**單機、單行程、純檔案儲存、完全非互動**的命令列工具。

黃金測試契約:`【契約編號11108】契約書(全).pdf`
(309 頁 / 正常 164 / 掃描 10 / 亂碼 135 / 9 個子文件 / 需求規範書 7 章)

---

## 一、Terminal 版與 Web 版的差異決策

| 項目 | Web 版(原設計) | Terminal 版(本設計) |
|---|---|---|
| 介面 | React 三欄畫面 | CLI 子命令 + 終端表格輸出 |
| 佇列 | Redis + Celery | 不需要(單行程循序執行) |
| 資料庫 | PostgreSQL(23 張表) | 純 JSON/JSONL 檔案 + manifest.json 狀態機 |
| 人工檢視 | 拖曳編輯 WBS Tree | `wbs inspect` 檢視 + 直接編輯 wbs.json 後 `wbs run --from validate` |
| 互動 | 點擊確認 | **零提示**:所有命令不問任何問題,警告寫入報告檔 |

其餘所有處理邏輯(15 個 Stage、資料模型、品質判定、家族偵測、
續表合併、LLM 五任務、驗證規則)**與最終架構報告完全一致**,
未來要升級成 Web 版時,stages/ 模組原封不動搬進 FastAPI worker 即可。

---

## 二、專案結構

```text
D:\wbs\
  pyproject.toml
  wbs.toml                  # 全域設定(LLM endpoint、門檻值)
  src\wbsgen\
    cli.py                  # typer 進入點,子命令定義
    manifest.py             # 狀態機:讀寫 manifest.json、判斷可跳過的 stage
    models.py               # Pydantic:Page/Block/Family/Subdoc/Section/Table/WorkItem/WbsNode
    stages\
      s01_parse.py          # PyMuPDF block/字型/座標 + pdfplumber 表格碎片
      s02_quality.py        # 頁面品質:常用中文字覆蓋率 + ToUnicode + 符號比例
      s03_layout.py         # 全域 Header/Footer Family(y 座標帶 + 連續頁段)
      s04_subdoc.py         # 子文件邊界(多訊號評分)
      s05_toc.py            # 目錄解析
      s06_section.py        # 正文標題定位 + Block 級章節切割
      s07_table.py          # Column Signature 續表合併 + 跨頁同列拼接
      s08_assemble.py       # 章節內容按閱讀順序重組(正文 + 表格 Markdown)
      s09_classify.py       # 規則分類 + LLM Task A 確認
      s10_extract.py        # LLM Task B 候選工作抽取
      s11_relate.py         # LLM Task C 工作關係建模
      s12_localwbs.py       # LLM Task D 局部 WBS
      s13_merge.py          # LLM Task E 全域合併 + 編碼
      s14_validate.py       # 結構/語意/來源/覆蓋四類檢查
      s15_export.py         # xlsx / json / mermaid / csv / coverage 報告
    llm\
      client.py             # OpenAI-compatible client,generate_json(schema 驗證+retry)
      prompts\              # 版本化 prompt 模板(不寫死在程式碼)
        classify_content_v1.txt
        extract_work_items_v1.txt
        build_relations_v1.txt
        generate_local_wbs_v1.txt
        merge_global_wbs_v1.txt
        validate_wbs_v1.txt
    textutil.py             # normalize_heading、頁碼剝除、常用中文字集
  tests\
    unit\                   # 正規化/簽名/家族 pattern/跨頁列合併
    golden\
      expected.json         # 11108 契約 ground truth
      test_golden.py
  data\projects\<id>\       # 執行產物(見第四節)
```

---

## 三、CLI 命令規格

工具名稱:`wbs`(console_script 進入點)。
**所有命令一律非互動**:不出現任何 y/n 提示;需要人工裁決的項目
(如 merge_confidence 不足的續表)寫入 `NEEDS_REVIEW` 清單後繼續跑。

```text
wbs init
    在目前目錄建立 wbs.toml 與 data/ 骨架。已存在則不動(冪等)。

wbs ingest <pdf路徑> [--project <id>]
    複製 PDF 至 original/(唯讀)、計算 SHA-256、建立 manifest。
    未指定 --project 時以檔名+日期自動命名。輸出 project id 到 stdout。

wbs run <project> [--from <stage>] [--to <stage>] [--stage <stage>] [--force]
    依序執行 stages。預設從第一個未完成的 stage 跑到 export(可斷點續跑)。
    每個 stage 完成即落地產物並更新 manifest;已完成且輸入 hash 未變者自動跳過。
    --force 忽略快取重跑。stage 名稱:parse quality layout subdoc toc section
    table assemble classify extract relate localwbs merge validate export

wbs auto <pdf路徑>
    懶人一鍵:ingest + run + export 全部跑完,結尾印出覆蓋率摘要與輸出檔路徑。

wbs status <project>
    印出各 stage 狀態表、頁面分類統計(正常/掃描/亂碼/混合)、
    子文件清單、NEEDS_REVIEW 數量。

wbs inspect <project> <view> [--id <物件id>]
    view = pages | subdocs | sections | tables | items | wbs | issues | trace
    trace --id wbs-3-2:顯示該節點 → work_items → 表格列 → 頁碼/原文引文的完整回溯鏈。

wbs rerun <project> --stage <stage> [--section <sec-id>] [--table <tbl-id>]
    局部重跑:只重算指定章節的抽取、或指定表格的續表合併,
    下游受影響的 stage 自動標記為 stale。

wbs export <project> [-f xlsx,json,mermaid,csv]
    預設全部格式。輸出檔名含產生時間與 WBS 版本。

wbs goldtest [--pdf <11108契約路徑>]
    對黃金契約完整跑一輪,與 tests/golden/expected.json 比對:
    頁面分類數、子文件邊界、章節數=7、壹貳同頁分割、Running Header 排除、
    表1~表10 辨識、表5 跨頁合併。任何退化即非零 exit code。
```

**Exit codes**:`0` 全部成功 / `2` 完成但有 NEEDS_REVIEW 或未解析區段(可用於 CI 判斷)/ `3` stage 失敗(manifest 記錄失敗原因,修復後 `wbs run` 從斷點續跑)。

---

## 四、產物佈局(取代 PostgreSQL)

每個 stage 的輸出即是下個 stage 的輸入,全部落地、全部可重跑:

```text
data\projects\<id>\
  manifest.json            # 狀態機:每 stage 的 status/input_hash/時間/錯誤
  original\contract.pdf    # 唯讀
  01_parse\pages\p0001.json …   blocks.jsonl   fonts.json
  02_quality\page_quality.jsonl   summary.json
  03_layout\families.json
  04_subdoc\subdocs.json
  05_toc\toc.json
  06_section\sections.json
  07_table\fragments.jsonl   tables.json     # 含 cross_page_merged、NEEDS_REVIEW
  08_assemble\sec-XXX.md                     # 送 LLM 的實際內容(可人工目視)
  09_classify\classifications.json
  10_extract\work_items.jsonl
  11_relate\relations.json
  12_localwbs\<語意區域>.json
  13_merge\wbs.json                          # 最終 WBS(人工可直接編修)
  14_validate\report.json                    # 四類檢查結果 + NEEDS_REVIEW 彙總
  exports\wbs_<版本>_<時間>.xlsx / .json / .mmd / .csv   coverage.txt
  llm_logs\<run_id>.json   # model/prompt版本/參數/input_hash/輸出/token/耗時
```

資料模型(Page/Block/LayoutFamily/Subdocument/Section/TableFragment/
Table/WorkItem/Relation/WbsNode)欄位定義**完全沿用最終架構報告
第七~十七節的 JSON 結構**,以 Pydantic 定義並在每次落地時驗證。

---

## 五、設定檔 wbs.toml

```toml
[llm]
base_url = "http://<內部推論主機>:8000/v1"   # vLLM OpenAI-compatible
model = "gemma-4-31b-it"
api_key = "internal"
max_input_tokens = 60000        # 256K 只當安全上限
temperature_extract = 0.0       # Task A/B/C
temperature_wbs = 0.1           # Task D/E
max_retries = 3                 # 壞 JSON:schema 驗證 → 帶錯誤訊息 retry

[quality]
min_chars = 30                  # 低於此 → IMAGE_ONLY/EMPTY
common_han_min = 0.55           # 常用中文字覆蓋率門檻(主訊號)
symbol_max = 0.50               # 符號比例上限(輔助)
check_tounicode = true          # 字型 ToUnicode 檢查(結構性訊號)

[layout]
header_band = 0.12              # 頁面頂部 12% 為頁首區
footer_band = 0.12
min_consecutive_pages = 2       # 連續 N 頁重複即判 running header/footer

[table]
merge_confidence_min = 0.80     # 低於此 → NEEDS_REVIEW,不強行合併

[spec_anchors]                  # 需求規範書相近名稱(不寫死單一名稱)
names = ["需求規範書","需求規格書","工作說明書","採購需求說明","委託服務需求書","技術需求規格"]
```

---

## 六、非互動原則(對應「懶得一直點確認」)

1. 任何 stage 都不彈提示;歧義項目降級為 `NEEDS_REVIEW` 記錄後繼續。
2. 亂碼/掃描頁自動標記跳過,絕不送入 LLM;數字誠實顯示在 coverage.txt。
3. `wbs auto` 一條命令從 PDF 到 Excel,中途唯一會停的原因是 stage 失敗(exit 3)。
4. 失敗後修復重跑不需人工選擇:manifest 自動從斷點續跑,已完成 stage 靠 input hash 跳過。
5. 人工修改 WBS 的方式:直接編輯 `13_merge\wbs.json`(MANUAL 節點),
   再 `wbs run <id> --from validate` 重驗證+重匯出,不會被自動生成覆蓋
   (merge stage 遇到 `generation_type=MANUAL` 或 `locked=true` 的節點一律保留)。

---

## 七、實作順序(對應 Phase 1 的 17 項)

```text
第 1 步  骨架:cli.py + manifest + models + init/ingest/status     (半天)
第 2 步  s01 parse + s02 quality        → goldtest 驗頁面分類 164/10/135
第 3 步  s03 layout + s04 subdoc        → goldtest 驗 9 個子文件邊界
第 4 步  s05 toc + s06 section          → goldtest 驗 7 章 + 壹貳同頁
第 5 步  s07 table + s08 assemble       → goldtest 驗表1~10 + 表5 跨頁合併
第 6 步  llm client + s09~s13           → 第一份 WBS JSON
第 7 步  s14 validate + s15 export      → Excel/Mermaid/coverage
每一步完成都跑 wbs goldtest,任何退化立即可見。
```
