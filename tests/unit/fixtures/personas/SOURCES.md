# Persona 迴歸語料的出處

這三份是**未經修改的第三方原文**，用來守住 `core/personas.py` 的抽取品質。

## 為什麼收真原文，而不是自己寫合成 fixture

合成 fixture 想不出真實世界的形態。2026-09-12 修抽取器時，三個缺陷裡有兩個
是只有真檔案才有的：

* **區塊引言**：每個心智模型開頭都有一句名言 `> "…"` 與出處署名 `> —— …`，
  它們原本會排在 `thinking_style` 的第一、二條。自己寫測試檔不會想到要放名言。
* **子樹配額**：一個心智模型底下有 `一句话`／`来源证据`／`应用方式`／`局限`
  四五個子章節，`模型1` 一個人就吃光整個欄位的額度。合成檔的章節結構太扁，
  這個缺陷根本浮不出來。

## 為什麼是這三份

各自代表一種形態，加起來才涵蓋得到三條驗收條件：

| 檔案 | 選它的理由 |
|------|-----------|
| `feynman-skill.md` | 結構最完整（5 個心智模型 × 5 個子章節），區塊引言雜質最明顯 |
| `mrbeast-skill.md` | 異常案例：`communication_style` 抽到 0 條 |
| `zhangxuefeng-skill.md` | 異常案例：`avoid` 0 條、`description` 只留得下一句 |

其餘 11 份人物 skill 沒有收進版控——三份足以守住規則，14 份只是讓每次改規則
都要核 14 份 snapshot。完整的 14 份一次性驗證紀錄見 2026-09-12 的工作紀錄。

## 出處與授權

全部取自 <https://github.com/alchaincyf>，MIT 授權，**逐字未改**，
路徑都是各 repo 的根目錄 `SKILL.md`。版本釘在下列 commit：

| 檔案 | 來源 repo | 預設分支 | commit |
|------|-----------|----------|--------|
| `feynman-skill.md` | `alchaincyf/feynman-skill` | `main` | `3ec26652e3b25c464a3188aeb2365445ce4f3d1d` |
| `mrbeast-skill.md` | `alchaincyf/mrbeast-skill` | `master` | `f63f0c5d4a2040ebf3540ec336a67a8bff12fd94` |
| `zhangxuefeng-skill.md` | `alchaincyf/zhangxuefeng-skill` | `main` | `3501b1e679cf595a6af800619af757aba46d7574` |

> MIT License — Copyright (c) 2026 Huashu (花叔)

要重新取得任一份：

```bash
curl -fsSL https://raw.githubusercontent.com/alchaincyf/<repo>/<commit>/SKILL.md
```

**更新語料要連同 commit 一起更新這張表**，否則「這份檔案是哪個版本」就查不出來，
而抽取結果變了也分不清是規則改了還是來源改了。

## 沒有收進來的那一份

`alchaincyf/sun-yuchen-perspective` 也是同一批人物 skill，但它的 repo
**沒有授權檔**（`gh api repos/alchaincyf/sun-yuchen-perspective --jq .license`
回 `null`）。沒有授權就沒有散布許可，所以不收進版控。
