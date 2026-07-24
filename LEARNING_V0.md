# LEARNING_V0 — 學習系統最小閉環(Terminal 執行任務)

> 先讀 CLAUDE.md(環境與守則)、LEARNING.md(整體設計)、
> grok.md 附錄九~十三(三輪評審定案,本任務的規格依據)。
> 本文件只含**已定案**的工作,依序執行 P1→P4,不要自行擴大範圍。

## 全域規則

- 在 `learning-system` 支線工作(已含本文件與設計文件)。
- 零互動;全程 mock LLM(本任務四步都不需要真模型)。
- 每步收尾:跑該步驗收 + `python -m pytest tests/ -x -q` 全綠才進下一步;
  commit 訊息「P<n>: <內容>」。
- `tests/golden/contract_11108.pdf` 唯讀;`data/`、本機路徑、
  `wbs.local.toml` 不得進 git。
- **明確不做**(另有任務,勿碰):孤兒工作項歸位、s13 merge 消費、
  s11 關聯層、回流提議器、三層繼承、Web UI、L2–L4 的標註 CLI。

---

## P1:Profile v0 — 規則外部化

### 做什麼
1. 新增 `profiles/default.toml`(格式定案為 TOML):把 s04_subdoc、
   s06_section、s09_classify 內的**全部業務字面量**逐條搬入。
   Schema 分塊(欄位名可微調,語意不可變):

   ```toml
   profile_id = "default"          # 市府共通(內容=今日 11108 規則)
   version = 1
   # extends 預留,v0 不實作繼承

   [subdoc]
   # title_anchors: [{pattern, min_font, toc_reject}] — 附件N、需求規範書錨點等
   # signature_anchors[]           — 立契約人 等
   # tender_anchors[]              — 招標公告特徵
   # footer_family_hints[]         — 「投標」「須知」等頁尾關鍵詞白名單
   # header_family_hints[]         — 評選日期 {n}/{n}/{n} 等
   # law_box_chars                 — │├─ 框線字元
   # quality_zone_map              — IMAGE_ONLY→SCANNED_PAGES, GARBLED→SERVICE_PROPOSAL

   [section]
   # 每 doc_type 一組編號體系:{pattern, min_font, numeral_set}
   # REQUIREMENT_SPECIFICATION: 壹貳參… / CONTRACT_BODY: 第N條 /
   # ATTACHMENT: 附件N： / BID_INSTRUCTIONS: 混合

   [classify]
   # excluded_doc_types[]
   # title_rules[]                 — regex → category(現行關鍵詞表,含順序語意:
   #                                 安全|資安 在 功能|系統 之前)
   # priority rules                — PRIMARYPRIMARY/SECONDARY/EXCLUDED + 附件特例

   [quality]   # 預留空欄位(garbled_signals),v0 不搬 s02
   [layout]    # 預留空欄位(band 參數),v0 不搬 s03

   [conservative]  # 保守模式最少集,v0 只定義欄位與預設值
   # use_profile = "default" / threshold_delta / forbid_long_forward_fill
   # always_emit_annotation
   ```

2. 新增 `src/wbsgen/profile.py`:Pydantic 模型 + 載入器
   (找不到 profile 檔 → 明確報錯,不得靜默退回硬編碼)。
3. s04/s06/s09 改為讀 profile 執行;**每個欄位加註來源對照**
   (欄位 ← 原檔案+函式),或另立 `profiles/MAPPING.md`。

### 驗收(全部自動化,常駐測試,不是一次性檢查)
- [ ] goldtest 25 項全綠(行為不變)
- [ ] **金絲雀**:載入 default.toml,對已知樣本字串
      (如「附件2:價金給付條件一覽表」「第十二條 驗收及查驗」)
      斷言 anchor/pattern 命中
- [ ] **反金絲雀**:測試中以覆寫後的 profile(某錨點改為不可能字串)
      跑對應 stage,斷言結果變化(如附件 section 數歸零)——
      證明 stage 真的在讀 profile
- [ ] **字面量靜態檢查**:掃描 `src/wbsgen/stages/s04*.py|s06*.py|s09*.py`
      原始碼,deny-list(附件、立契約人、投標、須知、評選、壹、第.*條、
      價金、驗收 等業務詞)零出現;例外需寫入 allowlist 並附理由註解

Commit:「P1: profile v0 — s04/s06/s09 規則外部化 + 金絲雀/反金絲雀/靜態檢查」

---

## P2:案例 Schema + 11108 案例遷移(與 P3 同週交付)

### 做什麼
1. `src/wbsgen/cases.py`:Pydantic 模型。**source 與 status 是正交兩欄**:
   ```
   case_id, pdf_sha256
   source: golden | corrected | auto      # 答案從哪來
   status: draft | active | disputed | retired   # 治理狀態
   answer_schema_version: 1
   labels:
     L1: [{page_start, page_end, doc_type, title?}]     # 子文件切分
     L2: [{page, title_norm, doc_type}]                 # 章節錨點
     L3: [{doc_type 或 title_norm, priority}]           # 排除/優先級
     L4: [{caption_norm, page_start, page_end}]         # 具名表格
   fingerprint: 純結構(總頁數、每頁字數/圖數、quality 序列、
                字型集合雜湊、家族 pattern 雜湊)
   provenance: {annotator, annotated_at, reviewer?, reviewer_same?: bool}
   ```
   **硬規則**:labels 禁止存執行期 id(subdoc-004、p0072-b003 都不行);
   L2 同頁多章用 (page, title_norm) 定位,評測時動態解析回 block。
   pdf 實體位置不進案例檔——另以 gitignore 的
   `cases/locators.local.toml` 存 {pdf_sha256 → 本機路徑}。
2. 遷移:`tests/golden/expected.json` → `cases/case-11108.json`
   (source=golden, status=active),指紋從 `data/projects/gold`
   的既有產物計算。

### 驗收
- [ ] 模型 round-trip 測試(dump→load 相等)
- [ ] **指紋無原文測試**:斷言 fingerprint 序列化後不含任何 CJK 字元
- [ ] case-11108.json 通過驗證且 labels 覆蓋 L1(10 子文件)+
      L2(需求規範書 7 章)+ L3(排除四類)+ L4(表5)
- [ ] `cases/locators.local.toml` 在 .gitignore 內

Commit:「P2: 案例 schema(source×status 正交、無執行期 id)+ 11108 遷移」

---

## P3:評測器 v0

### 做什麼
`wbs eval run`(新子命令):
1. 逐案例:由 locators.local.toml 找 PDF →
   **缺檔 → status=SKIPPED(不是失敗)**;
   檔在但 sha256 不符 → status=FAIL(防同名不同檔);
   檔在且符 → 跑 s01–s08(mock,重用 manifest 快取)→ 計分。
2. 計分(對 frozen labels):L1 子文件切分 F1(以頁範圍+doc_type 匹配)、
   L2 章節命中率、L3 排除正確率、L4 表格範圍正確率。
3. 記分卡 `eval_scorecard.json` + 終端表格:
   ```
   per-case: {case_id, status: ran|skipped|fail, skip_reason?,
              frozen: {L1_f1, L2_hit, L3_acc, L4_acc},
              adjudicated: null}      # 雙欄結構先建,v0 可空
   aggregates: {micro, macro, by_unit: [{unit, n, low_n}], skipped_count}
   merge_gate: {any_active_below_floor, macro_regressed, decision}
   ```
   merge_gate 的 baseline 取自上一次 committed 的記分卡
   (`cases/baseline_scorecard.json`,首跑時生成)。
4. CI/測試分層:pytest 只跑「合成小 PDF + schema 驗證 + SKIPPED 語意」;
   全庫記分卡是人工/工作站指令,不進測試套件。

### 驗收
- [ ] case-11108 跑分:L1 F1 = 1.0(其餘層記錄實際值,寫入 baseline)
- [ ] 人為改壞 locators(指向不存在路徑)→ SKIPPED + skipped_count=1,
      exit code 0
- [ ] 假 PDF(sha 不符)→ 該案例 FAIL,exit code 非 0
- [ ] 記分卡 schema 通過 Pydantic 驗證

Commit:「P3: eval v0 — 分層記分卡、SKIP/FAIL 語意、merge_gate 骨架」

---

## P4:修正 CLI v0(只做 L1 垂直切片)

### 做什麼
三個子命令,**只支援 L1 子文件切分層**(L2–L4 在 schema 已預留,CLI 不做):
1. `wbs review list <project>`:列出該專案的 NEEDS_REVIEW 與
   低信心項(現有訊號:表格 merge_confidence、切分空隙/未涵蓋頁)
2. `wbs review annotate <project>`:產出預填標註檔
   `review/annotation_L1.xlsx`——每列一個子文件:
   系統猜測的 page_start / page_end / doc_type(下拉選單限 enum)/
   title,外加空白列供人新增;同時輸出等價 YAML 供工程師用
3. `wbs review accept <project>`:讀標註檔 → 驗證(頁範圍連續無重疊、
   doc_type 合法)→ 寫入 `cases/case-<id>.json`
   (source=corrected,**status=draft**)→ 終端提示:
   「案例已入庫為 draft。profile 修改請人工編輯 profiles/*.toml,
   合併前必跑 wbs eval run 確認 merge_gate=allow」
   (promote 到 active 的指令本期不做,手改 status 欄位即可)

### 驗收
- [ ] 整流程自動化測試:對 gold 專案 annotate → 測試程式修改 xlsx 一列
      → accept → 案例檔存在、source=corrected、status=draft
- [ ] 壞標註(頁範圍重疊/非法 doc_type)→ 明確錯誤訊息 + 非零 exit,
      不寫入任何檔案
- [ ] xlsx 的 doc_type 欄有資料驗證(下拉),非工程人員可操作

Commit:「P4: correct CLI v0 — L1 標註閉環(list/annotate/accept→draft)」

---

## 完成定義(整體)

- P1–P4 各自驗收全過、四個 commit 依序存在
- `python -m pytest tests/ -q` 全綠(含新增的金絲雀/靜態檢查/schema 測試)
- 最終驗證劇本跑通:改 profiles/default.toml 一條錨點 → goldtest 紅
  → 改回 → 綠 → `wbs eval run` → case-11108 記分卡生成 →
  `wbs review annotate` + accept → draft 案例入庫
- 完成後產出 `reports/learning_v0.md`(本機,不進 git):
  各步驗收結果、記分卡 baseline 數字、已知限制
