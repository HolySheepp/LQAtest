# LQA Checker

遊戲在地化品質檢查工具。用螢幕 OCR 把遊戲實際顯示的對白抓下來，再跟翻譯文本對答案，
找出未套用翻譯、超框截斷、文字不一致、順序錯誤、缺句與發話者錯誤。

檢查方向是單向的：**使用者給的翻譯文本是正確答案，遊戲畫面是待檢查的對象。**

目標環境：Windows + 雷電模擬器，目標語言先做英文。

## 為什麼分成兩個階段

```
階段一  record    玩遊戲，把畫面上的每一句話記下來（不比對）
階段二  compare   離線把整段錄製對到翻譯文本，分類並輸出報告
```

邊玩邊比對有兩個問題：順序與缺句必須整段跑完才判斷得出來；而且比對一旦失準就得重玩一次。
拆開之後，錄製只做一次，比對可以反覆重跑、調參數、換門檻，不用再開遊戲。

## 安裝

在**專案資料夾**裡執行（就是這個 README 所在的資料夾）：

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install -r requirements-ocr.txt
```

直接指定 `.venv/Scripts/python.exe` 就不必先 activate，PowerShell 與 Git Bash 都通。

只要跑比對（例如重新分析已錄好的 session）的話，裝 `requirements.txt` 就夠了。
OCR 那包約 100MB，首次執行時還會再下載一次模型檔。

注意 opencv 必須裝**完整版** `opencv-python`，不能裝 `opencv-python-headless`。
headless 版沒有 GUI，`calibrate` 的框選視窗與遮罩預覽會開不起來。

## 檔案放哪

```
LQA/
  config/
    profile.json      calibrate 產生，不要手動編（可手動微調門檻）
    speakers.csv      發話者中英對照表，自己建，格式見 speakers.example.csv
  scripts/
    你的翻譯文本.xlsx   從 Google 試算表下載成 xlsx 放這裡
  sessions/           錄製結果，程式自動建立
  reports/            比對報告，程式自動建立
```

`config/profile.json`、`config/speakers.csv`、`scripts/`、`sessions/`、`reports/`
以及所有 `.xlsx` / `.csv` 都已經在 `.gitignore` 裡，**遊戲文本不會被推上 GitHub**。

路徑其實可以放任何地方，上面只是建議；實際位置在指令用 `--script` 等參數指定即可。

## 使用流程

### 1. 校準（每個解析度做一次）

先開好雷電模擬器並讓遊戲停在有對白的畫面，然後：

```bash
python -m lqa windows
```

找出雷電視窗的標題，接著框選對白框與姓名框：

```bash
python -m lqa calibrate --window 雷電模擬器 --profile config/profile.json
```

依序框選「對白框」與「姓名框」，接著會進入遮罩預覽。

**這一步是整套工具最關鍵的地方。** 遊戲對白框是半透明的、後面的背景會動，
如果直接比對原始像素，背景一動就會誤判成文字變了，一路狂觸發 OCR。
所以程式是先把 ROI 二值化成「只剩文字筆畫」的遮罩，再拿遮罩做變動偵測。
請在預覽畫面調到背景乾乾淨淨、只剩文字，再按 `s` 存檔。

```
1 / 2 / 3   切換二值化方式 (otsu / adaptive / bright)
i           反相（淺底深字時開）
c           開關 CLAHE 局部對比強化
[ / ]       調整 bright 門檻
- / =       調整 OCR 放大倍率
s           存檔離開      q / Esc  放棄離開
```

### 2. 錄製

把遊戲文字速度調到二倍速，然後正常手動遊玩：

```bash
python -m lqa record --profile config/profile.json --name ch1_school
```

程式會等文字停止變動才擷取，所以打字機效果不會造成半句被記錄。
終端機會即時列出抓到的每一句，按 Ctrl+C 結束。結果存在 `sessions/<時間戳>_ch1_school/`。

一章分好幾次錄沒問題，比對時把多個 session 依序傳進去即可。

### 3. 比對

準備發話者中英對照表（參考 `config/speakers.example.csv`），然後：

```bash
python -m lqa compare ^
  --script 2026_校園活動_全線文本_EN.xlsx ^
  --speakers config/speakers.csv ^
  --session sessions/20260911_143000_ch1_school ^
  --out reports/ch1_school
```

輸出 `reports/ch1_school.xlsx`（含摘要與明細，依分類上色）與同名 `.csv`。

比對前可以先確認文本讀得對：

```bash
python -m lqa check-script --script 你的文本.xlsx --speakers config/speakers.csv
```

## 分類定義

| 分類 | 判定方式 | 可靠度 |
| --- | --- | --- |
| 未翻譯/未套用 | 畫面出現中文字元；再拿中文原文欄反查是哪一句，報得出 ID | 高 |
| 超框 | 畫面文字是譯文的前綴，且長度明顯不足 | 高 |
| 不一致 | 同一個位置但文字對不上 | 中，需人工複核 |
| 順序不一致 | 單調對齊配不上，但交叉比對在別的位置找到 | 中高 |
| 遊戲中沒這句 | 整段跑完後，文本裡從未被對到的句子 | 中高 |
| 發話者錯誤 | 姓名框 OCR 結果與文本發話者英文名不符 | 中高 |
| 文本中無此句 | 畫面出現但文本找不到對應（額外的第七類，通常是 OCR 雜訊或漏給文本） | 低，需人工複核 |

**這份報告是「待複核清單」，不是「確定 bug 清單」。** OCR 一定有噪音，
「不一致」那一類尤其需要人眼看截圖確認。每一筆問題都有對應截圖路徑可以回查。

## 翻譯文本格式

支援 `.xlsx` / `.csv` / `.tsv`，Google 試算表可直接下載成 xlsx。

欄位靠**標題文字**辨識，不是靠欄位位置，所以欄位順序可以不同：

| 欄位 | 別名 | 必要 |
| --- | --- | --- |
| 對話ID | ID、對話編號、編號 | 是 |
| 英文翻譯 | 英文、English、EN、翻譯 | 是 |
| 名字 | 發話者、角色、speaker | 否 |
| 文字對話 | 中文、原文、source | 否（用於未翻譯反查 ID） |
| 備註 | note、remark | 否（不參與比對） |

處理規則：

- 標題列會自動尋找，不必在第 1 列
- 沒有對話ID 的列（場景說明、轉場註記）自動跳過
- **列順序就是期望播放順序，對話ID 不參與排序。** 實際文本會把後加的句子
  （例如 80208118）插在 80208010 與 80208011 中間，照 ID 排會錯

## 文字正規化

比對前兩邊都會拉到同一個基準，否則以下都會變成假 bug：

- 彎引號 `’` `“` 轉直引號、破折號 `—` `–` 統一、`…` 展開成 `...`
- 全形空白、不斷行空白、零寬字元
- 富文本標記 `<color=#FF0000>`、`[b]`
- 變數佔位符 `{playerName}`、`%s`、`%1$s`（只要求固定片段相符）
- 比對時移除所有標點，OCR 最常錯的就是標點
- OCR 固定誤判組 `l/I/1`、`O/0`、`rn/m` 折疊後取較高分

注意：**不能用「結尾是刪節號」當超框訊號**，因為專案譯文本身大量使用 `...` 結尾。
唯一可靠的訊號是前綴關係加上長度明顯不足。

## 已知限制

- 連續兩句文字完全相同時，若中間沒有經過空白畫面，第二句可能被當成重複而略過。
  這種情況會在報告中呈現為「遊戲中沒這句」，複核截圖即可確認。
- 對話有分支選項時，工具不會知道你選了哪一條；跑不同分支請分開錄製、分開比對。
- `mss` 後端是抓螢幕區域，模擬器視窗被其他視窗遮住時會抓到遮擋內容。
  若需要背景擷取或抓到黑畫面，需改接 Windows Graphics Capture 後端。
- 目前只針對英文調校。要加日韓需要換 OCR 語言模型，並調整正規化規則。

## 變動偵測的門檻怎麼定的

`stability.diff_threshold` 與 `rearm_threshold` 是「變動像素 / 文字像素量」的比例，
**不是佔 ROI 面積的比例**。在合成畫面（半透明框加會動的背景）上實測：

| 情況 | 佔 ROI 面積 | 佔文字像素量 |
| --- | --- | --- |
| 背景移動、文字不動 | 0.0001 ~ 0.0003 | 0.007 ~ 0.018 |
| 打字中的視窗累積量 | 約 0.004 | 約 0.13 |

文字只佔 ROI 面積 1% 出頭，用面積當分母會把訊號稀釋到跟背景雜訊同一個數量級，
所以改用文字量當分母，預設門檻 0.04 落在兩者中間，且不隨解析度或對白框大小改變。

另外偵測是比對「現在」與「stable_frames 幀之前」，不是只比前一幀 ——
打字打到空格時單幀變動量是 0，只比前一幀會誤判成已經穩定而錄到半句話。
`tests/test_capture_pipeline.py` 有這兩點的回歸測試。

## 專案結構

```
lqa/
  model.py              ExpectedLine / CapturedLine / Issue / Category
  config.py             profile：ROI、遮罩、穩定偵測、OCR 設定
  capture/              螢幕擷取（視窗定位、mss 後端）
  detect/               文字遮罩抽取、穩定幀偵測
  ocr/                  OCR 引擎抽象與 RapidOCR 實作
  record/               錄製主迴圈與 session 儲存
  compare/              正規化、文本讀取、序列對齊、分類、報告
  tools_calibrate.py    ROI 框選與遮罩預覽
  cli.py                命令列入口
tests/                  離線比對邏輯的測試（不需要遊戲即可跑）
```

## 測試

```bash
python -m pytest tests -q
```

測試涵蓋正規化、文本解析、序列對齊與六類分類，全部不需要遊戲或 OCR 即可執行。

## 後續規劃

- ADB 自動點擊：雷電可用 `adb shell input tap`，不需要視窗前景、不搶滑鼠焦點。
  現階段刻意不做，先讓人工點擊確保不會點錯而毀掉整輪錄製。
- Windows Graphics Capture 後端：支援背景擷取與被遮擋的視窗。
- GUI：把校準、錄製、比對包成單一視窗，讓非工程人員也能操作。
