

### Shokz 電量檢驗 POC（A5 5A / TLV over GATT：FEF2 寫入 → FEF1 通知）

* **用途：** 使用 Python（`bleak`）直接連上 Shokz 耳機，送出 GET 電量指令、解析回包，並印出「左耳 / 右耳 / 充電盒」電量。單一檔案，可獨立執行，不需 nRF Connect。

---

### 協定重點

* **指令通道：** 寫入 `0000FEF2-...`（Write Without Response），接收 `0000FEF1-...`（Notify）
* **指令框：** `A5 5A` + u16LE 全長 + `01 01 00 00` + u16LE TLV 長度 + u16LE CRC-16/MAXIM + TLV
* **TLV 結構：** `(tag1, len, 2, 4, cmdType)(3, 4, cmdId)(4, valueLen, value)`
* **回應框：** `responseType` @ 0x1C、`tag` @ 0x28（&0x7FFF）、`len` @ 0x30、`payload` @ 0x34
* **耳機電量（`tag = 0x03`）：** `payload = [status, 左 raw, 右 raw, ?]`
* **充電盒（`tag = 0x0B`）：** `payload = [status, 盒 raw, ?, ?]`
* **Raw 轉換為百分比：**
  * `0..9` $\rightarrow$ `(raw + 1) * 10`
  * `10..100` $\rightarrow$ `raw`
  * `> 100` $\rightarrow$ `200`（App 哨兵值）

---

### 用法
  - python3 shokz_battery_poc.py                     # 掃描並連上名稱含 "Shokz" 的裝置
  - python3 shokz_battery_poc.py OpenFit             # 以名稱關鍵字找裝置
  - python3 shokz_battery_poc.py AA:BB:CC:DD:EE:FF   # 直接指定位址（或 macOS 的 UUID）
  - python3 shokz_battery_poc.py --scan              # 只列出附近 BLE 裝置
  - python3 shokz_battery_poc.py --selftest          # 離線驗證解析邏輯（用實機錄到的回包）
  - python3 shokz_battery_poc.py --raw               # 除錯：傾印所有通知的原始 hex
  - python3 shokz_battery_poc.py --eq                # 同時查詢 EQ（模式 + 五帶數值）
  - python3 shokz_battery_poc.py -t 5                # 每個指令的等待秒數（預設 3）

---

### 依賴

* `pip install bleak`（僅 live 模式需要；`--selftest` 不需要，可離線執行。）
