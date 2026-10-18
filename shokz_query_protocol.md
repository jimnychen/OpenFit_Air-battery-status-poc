# Shokz A5 5A 指令封包產生器

## 簡介

這個腳本用來產生通用的 Shokz A5 5A 指令封包。輸入 `cmdType`（例如 `GET = 2`）與 `cmdId`，即可輸出完整的封包 HEX。這段內容可直接貼到 nRF Connect 中，寫入 `FEF2`（`0000FEF2-0000-1000-8000-00805F9B34FB`）。同時也可訂閱 `FEF1` 來接收回應。

## 封包格式

以下封包格式已經與實機抓到的資料逐 byte 驗證過。

### 標頭（12 bytes）

- `A5 5A` — magic
- `u16LE n + 0x2C` — 全框長（`= 總長度 - 8`）
- `01 01 00 00` — `[6] = protocolType`（`0 = 指令`）
- `u16LE n + 0x28` — TLV 長度
- `u16LE CRC-16/MAXIM(TLV)` — TLV 的 CRC（little-endian）

### TLV 結構

每個欄位都是 `u32LE` 的 tag 或 length。

```text
01 00 00 00  (0x20+n) 00 00 00       ← tag1，內含長度 = 0x20+n
  02 00 00 00  04 00 00 00  cmdType 00 00 00    ← tag2 = cmdType（GET=2）
  03 00 00 00  04 00 00 00  cmdId   00 00 00    ← tag3 = cmdId
  04 00 00 00  n  00 00 00  value[n]            ← tag4 = value（GET 時為空）
```

## 用法範例

```bash
python3 make_shokz_query.py --cmd 2 --cmdid 0x08        # EQ
python3 make_shokz_query.py --cmd 2 --cmdid 0x03        # 耳機電量
python3 make_shokz_query.py --cmd 2 --cmdid 0x0B        # 充電盒電量
python3 make_shokz_query.py --cmd 2 --cmdid 0x02        # 版本
python3 make_shokz_query.py --cmd 2 --cmdid 0x08 --single   # 單行輸出
python3 make_shokz_query.py --selftest                  # 與實機驗證封包比對
```

## 備註

這個腳本適合用於協定分析與驗證。例如在確認原始封包是否符合實際硬體行為時，或是在測試 Shokz 裝置的 Bluetooth LE 指令路徑時使用。
