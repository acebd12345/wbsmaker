# 第三輪任務:讓真模型產出「有血肉」的 WBS

前情:真模型首輪(commit `1e9950a`)管線跑通、0 壞 JSON、trace 10/10 有效、
測試全綠。但成品 WBS 只有 31 節點 2 層——章節骨架而已,工作項目沒有掛上去。
`reports/real_vs_mock.md` 已誠實記錄原因。本輪修四件事,全部是程式側修正。

---

## 修正 1:禁止截斷,改成分段呼叫(最重要)

現況:為了迴避端點對大輸入的 504,extract 把輸入砍到 2000 字元。
「肆、維運服務」全章 29KB 只送了前 2000 字——表3/表5 的幾十列工作項目全數丟失,
整份契約只抽出 28 個 work item(每章 1 次呼叫),這就是 WBS 空洞的根源。

DESIGN.md 第十六節明定:超過門檻要**依語意單元分割成多次呼叫**,不是截斷。

**修法**:
- 章節超過安全輸入長度(先取 2500 字元,避開 504)→ 依次標題/段落/表格切成
  多個 chunk,**表格以列為單位分批、每批附表頭與 caption**,逐 chunk 呼叫後合併結果。
- 任何 request 的 input 都不可以是被截斷的文本;字數不夠塞的丟到下一個 chunk。
- 504 處理:退避重試 2 次,仍失敗則把該 chunk 再對半切,直到成功或單 chunk 已小於
  500 字元(此時記入 validation report,不得靜默丟棄)。

**驗收**:表5 的每一列至少產生一個 work item 或被明確記入未抽取清單;
work item 總數應顯著超過 28(合理量級:百上下);肆、維運服務章的 llm input
總字元數 ≈ 全章字元數(允許 chunk 重疊表頭),不再是 2000。

## 修正 2:localwbs / merge 失敗不得靜默退回規則版

現況:s12/s13 的真模型呼叫失敗被 try/except 吞掉,悄悄退回 rule-based,
造成「真模型版 WBS」其實根本沒經過真模型組織。

**修法**:LLM 呼叫失敗遵守修正 1 的重試/對切策略;最終仍失敗 → stage 標
FAILED 落地錯誤,exit code 3,不准無聲 fallback。(rule-based 可保留為
`--fallback-rules` 顯式旗標,預設關閉。)

**驗收**:llm_logs 中 localwbs 與 merge 有成功的真模型呼叫記錄;產出的 WBS
層數 ≥ 3(章節下掛工作包/工作項目);維運服務節點 children > 0,
且含表5 來源的工作項目。

## 修正 3:輸出語言與 category 枚舉鎖死

- 所有 prompt 明文要求:title/description 一律**繁體中文**(source_text 保持原文)。
- schema 的 category/item_type 用 enum 鎖成 DESIGN.md 的值(WORK/DELIVERABLE/
  MILESTONE/MEETING/ACCEPTANCE/PAYMENT/TRAINING/…),不接受自由字串。
- classify prompt 補 3~5 個分類範例(few-shot),目標把 UNCLASSIFIED 從 22% 壓低。

**驗收**:抽 20 個 work item 與 20 個 WBS 節點,標題全為中文;
category 全部落在 enum 內;UNCLASSIFIED ≤ 10%。

## 修正 4:附件2/附件9 的付款與驗收里程碑必須進 WBS

現況:28 個 work item 裡看不到「按季查驗」「教育訓練每年 1,4,7,10 月定期查驗」
「最後一期結案驗收後付款」這類節點。附件2(價金給付條件)、附件9(驗收及查驗規定)
在 p50/p57,是 PAYMENT_MILESTONE / ACCEPTANCE 的直接來源,必須被分類為 PRIMARY
並完整抽取。

**驗收**:WBS 中存在 PAYMENT 與 ACCEPTANCE 類節點,且 trace 回溯到附件2/附件9
所在頁面;維運服務的按季查驗、教育訓練的按次交付均有對應節點。

---

## 完成定義

1. 四項修正各一個 commit(「round3-N: <內容>」),原有 25 tests + 24 goldtest 不退化。
2. 真模型完整重跑黃金契約(`wbs rerun gold --stage extract` 起),
   更新 `reports/real_vs_mock.md`(附上與首輪的節點數/深度/token 對照)。
3. 匯出的 Excel 打開後是一份「看得出這是 i-Voting 維運案」的 WBS:
   章節下有具體工作、有付款/驗收里程碑、節點皆中文、皆可回溯。
4. 端點若 504 頻繁到無法完成:先完成程式側修正與 mock 驗證,
   在報告記錄 504 統計(次數/輸入長度分布),停下來等 nginx/vLLM 調整,不要空轉。

## 給使用者的話(不是給你的任務)

504 的根因在伺服器端 nginx/vLLM 的 timeout 設定,程式側只能繞。
若要根治,請機關管理 INTERNAL-LLM-HOST 的同事把 proxy read timeout 調大(如 300s)。
