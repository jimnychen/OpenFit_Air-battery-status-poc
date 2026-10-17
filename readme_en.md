
---

### Shokz Battery Check POC (A5 5A / TLV over GATT: FEF2 Write → FEF1 Notify)

* **Purpose:** Connect directly to Shokz earphones using Python (`bleak`), send a GET battery command, parse the response packet, and print the battery levels for "Left / Right / Charging Case". Single-file, standalone execution, no nRF Connect required.

---

### Protocol Highlights (Verified on real device)
* **Command Channel:** Write to `0000FEF2-...` (Write Without Response), Receive from `0000FEF1-...` (Notify)
* **Command Frame:** `A5 5A` + u16LE Total Length + `01 01 00 00` + u16LE TLV Length + u16LE CRC-16/MAXIM + TLV
* **TLV Structure:** `(tag1, len, 2, 4, cmdType)(3, 4, cmdId)(4, valueLen, value)`
* **Response Frame:** `responseType` @ 0x1C, `tag` @ 0x28 (&0x7FFF), `len` @ 0x30, `payload` @ 0x34
* **Earphone Battery (`tag = 0x03`):** `payload = [status, left_raw, right_raw, ?]`
* **Charging Case (`tag = 0x0B`):** `payload = [status, case_raw, ?, ?]`
* **Raw to % Conversion:**
* `0..9` $\rightarrow$ `(raw + 1) * 10`
* `10..100` $\rightarrow$ `raw`
* `> 100` $\rightarrow$ `200` (App sentinel value)



---

### Usage

* `python3 shokz_battery_poc.py` — Scan and connect to devices with "Shokz" in their name.
* `python3 shokz_battery_poc.py OpenFit` — Find device by name keyword.
* `python3 shokz_battery_poc.py AA:BB:CC:DD:EE:FF` — Directly specify the address (or macOS UUID).
* `python3 shokz_battery_poc.py --scan` — Only list nearby BLE devices.
* `python3 shokz_battery_poc.py --selftest` — Offline verification of parsing logic (using recorded live packets).
* `python3 shokz_battery_poc.py --raw` — Debug mode: Dump raw hex of all notifications.
* `python3 shokz_battery_poc.py -t 5` — Set wait time in seconds per command (default is 3).

---

### Dependencies

* `pip install bleak` (Only required for live mode; `--selftest` does not require it and can run offline).

---

Would you like me to help you write or structure the Python script (`shokz_battery_poc.py`) based on these protocol specifications?