# 抽查發現的缺陷(第一輪驗收)

s01–s08 的文件工程層驗證屬實:頁面分類 164/10/135、10 個子文件邊界、需求規範書
7 章(壹貳同頁 block 級切割正確,anchor p0072-b003 屬實)、表1~表10 與表5 跨頁合併
——goldtest 涵蓋的項目全部真實通過,這部分不要動。

但 s06(部分)與 s09–s13 有三個**程式側**缺陷。這些不是 mock LLM 的語意品質問題,
接上真 LLM 也一樣會產出錯誤結果,必須修:

---

## 缺陷 1:section_id 不是全域唯一,來源回溯鏈已損壞(最嚴重)

證據:`06_section/sections.json` 中 `sec-001`、`sec-003` 同時存在於 subdoc-001、
subdoc-004、subdoc-005。`10_extract/work_items.jsonl` 的項目只帶 `section_id`
不帶 subdoc,結果第一筆 work item 就是三個來源混在一起:

```json
{"item_id": "item-0001", "section_id": "sec-001",
 "description": "第一條 契約文件及效力.....3",      ← 契約本文目錄行
 "source_pages": [1],                              ← 錯誤頁碼
 "source_text": "十七、投標廠商應提出之資格證明文件…"  ← 投標須知的內文
}
```

DESIGN.md 的可追溯性原則(每個節點回溯到來源章節與原文)在這狀態下是壞的。

**修法**:section_id 改為全域唯一(建議 `{subdoc_id}-sec-{nnn}`);work_item 的
source_refs 必須含 subdoc_id + section_id + block/table_row id + 頁碼,且三者必須
互相一致(source_text 必須真的出自該 section 的頁面範圍)。

**新增 goldtest**:隨機抽 20 筆 work item,驗證其 source_text 確實出現在其
section 的起迄頁範圍內的 blocks 中;WBS 節點 trace 鏈逐層 id 都能解析。

## 缺陷 2:契約本文的 section 直接拿目錄行當章節,未回正文定位

證據:subdoc-001 的 section 標題是「第三條 契約價金之給付........................5」
——帶點線與頁碼的目錄行。需求規範書(subdoc-004)做對了(標題乾淨、anchor 在正文),
但契約本文走了另一條錯誤路徑。DESIGN.md Stage 6 明定:目錄只提供候選,必須到正文
fuzzy match 定位 block anchor。

**修法**:契約本文比照需求規範書流程:目錄項 → normalize(去點線、去頁碼)→
正文搜尋「第X條 …」標題 block → 建立 body anchor 的 section。

**新增 goldtest**:subdoc-001 的所有 section 標題不含 `....`;「第三條 契約價金之給付」
的 start anchor 落在 PDF p4–48 的正文範圍,而非 p2–3 目錄頁。

## 缺陷 3:Stage 10 的來源優先級(PRIMARY/SECONDARY/EXCLUDED)完全沒實作

證據:`09_classify/classifications.json` 只有 `category` 欄位,沒有
priority/wbs_relevance;於是投標須知、評選須知全部進了抽取,最終
`13_merge/wbs.json` 的第一層是「契約本文(16 子節點)/投標須知(26 子節點)」,
而維運服務、教育訓練、退場作業等真正的工作範圍**完全沒有出現在 WBS 中**。
投標/評選文件是投標階段的程序文件,依 DESIGN.md 十四節屬 Excluded by Default。

**修法**:實作 DESIGN.md Stage 10 的完整輸出(semantic_type、wbs_relevance、
priority);s10 只對 PRIMARY 與 SECONDARY 來源抽取;TENDER_INSTRUCTION、
EVALUATION_GUIDELINES、TENDER_ANNOUNCEMENT、LAW_OR_POLICY 子文件預設 EXCLUDED
(保留分類結果供稽核,但不送 LLM、不進 WBS)。

**新增 goldtest**:llm_logs 的所有 input 不含投標須知/評選須知內容;最終 WBS
包含標題含「維運」「教育訓練」「退場」的節點;第一層不得出現「投標須知」。

## 次要(順手修,不強制)

- mock 抽取的 work item `description` 目前是 section 標題;改成取該 section 內
  表格列或段落首句,mock 產出的 WBS 才有檢視價值。
- `wbs inspect trace` 既然缺陷 1 存在還能通過驗收,代表原本的 trace 驗收檢查太弱,
  修缺陷 1 時一併把該測試改成上述抽樣驗證。

---

---

# 第二階段:接真模型(缺陷修畢、goldtest 全綠後才能開始)

wbs.toml 的 `[llm]` 已填入內網真實端點(`https://INTERNAL-LLM-HOST/v1`,
model=`/main_model`,無需 API key,max_model_len=128k)。依序執行:

## 2-1 端點煙霧測試
先寫一個最小測試(或 `wbs llm-check` 子命令):呼叫 `/v1/models` 確認存活,
再發一個小的 chat completion(附 JSON schema 要求)驗證 `generate_json()`
對真端點能跑通 schema 驗證 + retry 流程。失敗就停下來把錯誤寫進報告,
**不要無限重試**(端點異常屬環境問題,不是程式能修的)。

## 2-2 真模型跑一輪黃金契約
```
wbs.toml 改 mock = false
wbs rerun gold --stage classify        # 讓下游全部 stale
wbs run gold --from classify --to export
```
守則:
- 亂碼頁與掃描頁絕不可出現在任何 request(原有 unit test 在真模式下也要跑)。
- 每次呼叫記 llm_logs(model、prompt 版本、input hash、token 數、耗時、重試次數)。
- 單次輸入超過 max_input_tokens 依 DESIGN.md 第十六節優先序切割,不可截斷。
- 若連續多個 request 失敗(逾時/5xx),中止並將已完成部分落地,報告失敗位置。

## 2-3 真模型 vs mock 對比報告
產出 `reports/real_vs_mock.md`,至少包含:
- 兩版 WBS 節點數、層數、第一層分支對照表
- 真模型版:維運服務/系統功能新增/教育訓練/退場作業分支是否齊全
- 抽 10 個 EXPLICIT 節點驗 trace(原文引文確實在來源頁面範圍內)
- token 總用量與總耗時(從 llm_logs 彙總)
- 發現的 prompt 問題清單(壞 JSON 率、重試率、明顯誤抽的例子)——只記錄,不擅自改 prompt

## 完成定義

1. 三個缺陷修畢:新增 goldtest 項目全綠、原有 15 項不退化,每缺陷一個 commit(「fix N: <內容>」)。
2. mock 模式 `wbs auto` 重跑:WBS 第一層出現需求規範書的工作分支,任一節點 trace 回溯到正確頁碼與原文。
3. 真模型跑通黃金契約全管線,`reports/real_vs_mock.md` 產出,對應 commit(「real-llm: 首輪真模型驗證」)。
4. 若第二階段因端點問題無法完成,完成第 1、2 項後停止並在報告中說明,不要空轉等待。
