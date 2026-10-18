### Shokz Battery Verification POC (A5 5A / TLV over GATT: FEF2 Write → FEF1 Notify)

* Purpose: Use Python (`bleak`) to connect directly to a Shokz headset, send a battery query command, parse the response, and print the battery levels for the left ear, right ear, and charging case. It is a single-file script that can run independently without nRF Connect.

---

### Protocol Highlights

* Command channel: Write to `0000FEF2-...` (Write Without Response), receive notifications from `0000FEF1-...`
* Command frame: `A5 5A` + u16LE total length + `01 01 00 00` + u16LE TLV length + u16LE CRC-16/MAXIM + TLV
* TLV structure: `(tag1, len, 2, 4, cmdType)(3, 4, cmdId)(4, valueLen, value)`
* Response frame: `responseType` at `0x1C`, `tag` at `0x28` (& `0x7FFF`), `len` at `0x30`, `payload` at `0x34`
* Earbud battery (`tag = 0x03`): `payload = [status, left raw, right raw, ?]`
* Charging case (`tag = 0x0B`): `payload = [status, case raw, ?, ?]`
* Raw value to percentage conversion:
  * `0..9` → `(raw + 1) * 10`
  * `10..100` → `raw`
  * `> 100` → `200` (app sentinel value)

---

### Usage

  - python3 shokz_battery_poc.py                     # Scan and connect to a device whose name contains "Shokz"
  - python3 shokz_battery_poc.py OpenFit             # Find a device by name keyword
  - python3 shokz_battery_poc.py AA:BB:CC:DD:EE:FF   # Connect directly to a specific address (or macOS UUID)
  - python3 shokz_battery_poc.py --scan              # List nearby BLE devices only
  - python3 shokz_battery_poc.py --selftest          # Offline verification of parsing logic using captured real-device responses
  - python3 shokz_battery_poc.py --raw               # Debug mode: dump raw hex for all notifications
  - python3 shokz_battery_poc.py --eq                # Query EQ at the same time (mode + five-band values)
  - python3 shokz_battery_poc.py -t 5                # Wait time per command in seconds (default: 3)

---

### Dependencies

* `pip install bleak` (required only for live mode; `--selftest` does not require it and can run offline.)