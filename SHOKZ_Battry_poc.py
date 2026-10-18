#!/usr/bin/env python3
"""Shokz 電量檢驗 POC（A5 5A / TLV over GATT：FEF2 寫入 → FEF1 通知）

用途：用 Python（bleak）直接連上 Shokz 耳機，送 GET 電量指令、解析回包，
      印出「左耳 / 右耳 / 充電盒」電量。單一檔案、可獨立執行，不需 nRF Connect。

協定重點（已實機驗證，詳見 shokz-ble-protocol-analysis.md §4.5 / §4.6 / §8.2）：
  - 指令通道：寫 0000FEF2-…（Write Request；預設 with response，逾時自動改用 WNR 重試）、
              收 0000FEF1-…（Notify）
  - 指令框  ：A5 5A + u16LE 全長 + 01 01 00 00 + u16LE TLV長 + u16LE CRC-16/MAXIM + TLV
  - TLV     ：(tag1, len, 2, 4, cmdType)(3, 4, cmdId)(4, valueLen, value)
  - 回應框  ：responseType @0x1C、tag @0x28 (&0x7FFF)、len @0x30、payload @0x34
  - 耳機電量 tag=0x03：payload = [status, 左 raw, 右 raw, ?]
  - 充電盒   tag=0x0B：payload = [status, 盒 raw, ?, ?]
  - raw→%   ：0..9 → (raw+1)*10；10..100 → raw；>100 → 200（App 哨兵值）
  - EQ      tag=0x08：payload = [status, mode, b0..b4, 00]，band 為二補數 s8、與 App 滑桿 1:1
              （mode：1=一般 2=人聲 3=低音增強 4=隱私保護 5=自訂；b0 = 65 Hz，五帶）

用法：
  python3 shokz_battery_poc.py                     # 掃描並連上名稱含 "Shokz" 的裝置
  python3 shokz_battery_poc.py OpenFit             # 以名稱關鍵字找裝置
  python3 shokz_battery_poc.py AA:BB:CC:DD:EE:FF   # 直接指定位址（或 macOS 的 UUID）
  python3 shokz_battery_poc.py --scan              # 只列出附近 BLE 裝置
  python3 shokz_battery_poc.py --selftest          # 離線驗證解析邏輯（用實機錄到的回包）
  python3 shokz_battery_poc.py --raw               # 除錯：傾印所有通知的原始 hex
  python3 shokz_battery_poc.py --eq                # 同時查詢 EQ（模式 + 五帶數值）
  python3 shokz_battery_poc.py -t 5                # 每個指令的等待秒數（預設 3）

依賴：pip install bleak（僅 live 模式需要；--selftest 不需，可離線跑）
"""
from __future__ import annotations

import argparse
import asyncio
import re
import struct
import sys

# ------------------------------------------------------------------ 協定常數
WRITE_CHAR = "0000fef2-0000-1000-8000-00805f9b34fb"   # 寫入指令（WNR）
NOTIFY_CHAR = "0000fef1-0000-1000-8000-00805f9b34fb"  # 接收回包（Notify）

CMD_GET = 2
GET_TAG_HEADSET = 0x03   # 耳機電量
GET_TAG_CASE = 0x0B      # 充電盒電量
GET_TAG_EQ = 0x08        # 當前 EQ（模式 + 五帶）

RESP_GET = 2             # responseType == 2

EQ_MODE_NAMES = {1: "一般模式", 2: "人聲模式", 3: "低音增強", 4: "隱私保護", 5: "自訂 EQ"}  # ✅ 實測


# ------------------------------------------------------------------ 編解碼
def crc16_maxim(data: bytes) -> int:
    """CRC-16/MAXIM-DOW（poly 0x8005 反射、init 0、xorout 0xFFFF）。"""
    crc = 0x0000
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return (crc ^ 0xFFFF) & 0xFFFF


def build_frame(cmd_type: int, cmd_id: int, value: bytes = b"") -> bytes:
    """組出一個 A5 5A 指令框。"""
    u32 = lambda v: struct.pack("<I", v)
    u16 = lambda v: struct.pack("<H", v & 0xFFFF)
    n = len(value)
    tlv = b"".join([u32(1), u32(n + 0x20), u32(2), u32(4), u32(cmd_type),
                    u32(3), u32(4), u32(cmd_id), u32(4), u32(n)]) + value
    return (b"\xA5\x5A" + u16(n + 0x2C) + b"\x01\x01\x00\x00" +
            u16(n + 0x28) + u16(crc16_maxim(tlv)) + tlv)


def get_headset_frame() -> bytes:
    return build_frame(CMD_GET, GET_TAG_HEADSET)


def get_case_frame() -> bytes:
    return build_frame(CMD_GET, GET_TAG_CASE)


def get_eq_frame() -> bytes:
    return build_frame(CMD_GET, GET_TAG_EQ)


def parse_response(data: bytes) -> dict | None:
    """把一筆 A5 5A 回應框解成 dict；非 TLV 指令框回 None。"""
    if len(data) < 0x34 or data[0x06] != 0x00:
        return None
    rtype = int.from_bytes(data[0x1C:0x20], "little")
    tag = int.from_bytes(data[0x28:0x2C], "little")
    if rtype != 6:                       # 除 responseType==6 外都要遮罩高位
        tag &= 0x7FFF
    plen = int.from_bytes(data[0x30:0x34], "little")
    payload = bytes(data[0x34:0x34 + plen])
    tlv_len = int.from_bytes(data[8:10], "little")
    crc = int.from_bytes(data[10:12], "little")
    return {
        "response_type": rtype,
        "tag": tag,
        "payload": payload,
        "crc": crc,
        "crc_ok": crc16_maxim(bytes(data[12:12 + tlv_len])) == crc,
    }


def raw_to_percent(raw: int) -> int:
    """App 的 raw→百分比換算（耳機 / 充電盒共用同一公式）。"""
    if raw <= 9:
        return (raw + 1) * 10
    if raw <= 100:
        return raw
    return 200                            # App 哨兵值（非真實電量）


def decode_battery(frame: dict) -> dict | None:
    """從解析後的框取出電量；非電量回應回 None。"""
    if frame["response_type"] != RESP_GET:
        return None
    p = frame["payload"]
    if frame["tag"] == GET_TAG_HEADSET and len(p) >= 3:
        return {"kind": "headset", "status": p[0],
                "left_raw": p[1], "right_raw": p[2],
                "left_%": raw_to_percent(p[1]), "right_%": raw_to_percent(p[2])}
    if frame["tag"] == GET_TAG_CASE and len(p) >= 2:
        return {"kind": "case", "status": p[0],
                "case_raw": p[1], "case_%": raw_to_percent(p[1])}
    return None


def decode_eq(frame: dict) -> dict | None:
    """從解析後的框取出 EQ 狀態；非 EQ 回應回 None。

    payload = [status, mode, b0..b4, 00]：mode 見 EQ_MODE_NAMES；
    b0..b4 = 五個帶（s8 二補數，與 App 自訂 EQ 滑桿 1:1；b0 = 65 Hz）。
    """
    if frame["response_type"] != RESP_GET or frame["tag"] != GET_TAG_EQ:
        return None
    p = frame["payload"]
    if len(p) < 7:
        return None
    bands = [b - 256 if b > 127 else b for b in p[2:7]]
    mode = p[1]
    return {"kind": "eq", "status": p[0], "mode": mode,
            "mode_name": EQ_MODE_NAMES.get(mode, f"未知({mode})"),
            "bands": bands, "reserved": p[7] if len(p) >= 8 else None}


# ------------------------------------------------------------------ 輸出
def print_report(results: dict) -> None:
    print("\n================= 結果 =================")
    head = results.get("headset")
    if head:
        print(f"  耳機   左耳 {head['left_%']:>3}%  (raw {head['left_raw']:>3})"
              f"   右耳 {head['right_%']:>3}%  (raw {head['right_raw']:>3})"
              f"   status={head['status']}")
    else:
        print("  耳機   未取得回應")
    case = results.get("case")
    if case:
        print(f"  充電盒 {case['case_%']:>3}%  (raw {case['case_raw']:>3})"
              f"   status={case['status']}")
    else:
        print("  充電盒 未取得回應（充電盒可能未連線/未喚醒）")
    eq = results.get("eq")
    if eq:
        bands = "  ".join(f"b{i}={v:+d}" for i, v in enumerate(eq["bands"]))
        reserved = f"  (保留={eq['reserved']:02X})" if eq["reserved"] is not None else ""
        print(f"  EQ     {eq['mode_name']}（mode={eq['mode']}）  {bands}{reserved}")
    elif results.get("_eq_requested"):
        print("  EQ     未取得回應")
    print("=======================================\n")


# ------------------------------------------------------------------ live 模式
def _looks_like_address(s: str) -> bool:
    if len(s) == 36 and s.count("-") == 4:            # macOS CoreBluetooth UUID
        return True
    parts = re.split(r"[:\-]", s)                      # AA:BB:CC:DD:EE:FF
    return len(parts) == 6 and all(len(p) == 2 for p in parts)


async def _find_target(filter_: str):
    from bleak import BleakScanner
    print(f"[*] 掃描 BLE 裝置（找 “{filter_}”）…")
    devs = await BleakScanner.discover(timeout=8.0, return_adv=True)
    for dev, adv in devs.values():
        name = adv.local_name or dev.name or ""
        if filter_.lower() in name.lower():
            print(f"[+] 找到 {name!r} ({dev.address})")
            return dev
    return None


async def read_once(filter_: str, raw: bool, timeout: float,
                    want_eq: bool = False, wnr_first: bool = False) -> int:
    from bleak import BleakClient

    if _looks_like_address(filter_):
        target, label = filter_, filter_
    else:
        dev = await _find_target(filter_)
        if dev is None:
            print(f"[-] 找不到名稱含 “{filter_}” 的裝置；可用 --scan 列出，或直接給位址。")
            return 2
        target, label = dev, (dev.name or dev.address)

    results: dict = {}
    events = {"headset": asyncio.Event(), "case": asyncio.Event(), "eq": asyncio.Event()}
    stats = {"notif": 0, "unparsed": 0}

    def on_notify(_sender, data: bytearray):
        blob = bytes(data)
        stats["notif"] += 1
        if raw:
            print("  <-", blob.hex(" "))
        frame = parse_response(blob)
        if not frame:
            if blob[:2] == b"\xa5\x5a":       # A5 5A 開頭卻解不開：多半被截斷/CRC 壞
                stats["unparsed"] += 1
                print(f"    [!] 收到 A5 5A 通知但無法解析（{len(blob)}B）：{blob.hex(' ')}")
            return
        for dec in (decode_battery(frame), decode_eq(frame)):
            if dec and dec["kind"] not in results:
                results[dec["kind"]] = dec
                events[dec["kind"]].set()

    print(f"[*] 連線 {label} …")
    async with BleakClient(target) as client:
        try:
            await client.pair()
            print("[+] 藍牙裝置配對/交握成功")
        except Exception as e:
            print(f"[-] 配對失敗或不支援自動配對: {e}")
            print("[+] 已連線；訂閱 FEF1 通知")
        await client.start_notify(NOTIFY_CHAR, on_notify)
        mtu = getattr(client, "mtu_size", None)
        if mtu:
            print(f"    MTU = {mtu}")

        requests = [("headset", "耳機電量", get_headset_frame()),
                    ("case", "充電盒電量", get_case_frame())]
        if want_eq:
            results["_eq_requested"] = True
            requests.append(("eq", "EQ 模式", get_eq_frame()))

        # 官方 App 對 FEF2 一律用 Write Request（with response）：預設先試 wr、
        # 逾時再換 wnr 重試；--wnr 則反過來。
        for kind, label_zh, frame in requests:
            events[kind].clear()
            print(f"[>] 送出 GET {label_zh}（{len(frame)}B）…")
            got = False
            try:
                await client.write_gatt_char(WRITE_CHAR, frame, response=("wnr"))
            except Exception as exc:
                print(f"    [!]  寫入失敗（{exc}）")
                continue
            try:
                await asyncio.wait_for(events[kind].wait(), timeout)
                got = True
            except asyncio.TimeoutError:
                pass
            if not got:
                print(f"    [-] 等待 {label_zh} 逾時（{timeout}s × 2 種寫入）")

        try:
            await client.stop_notify(NOTIFY_CHAR)
        except Exception:
            pass

    print(f"[i] 期間收到 {stats['notif']} 筆通知（A5 5A 開頭但無法解析：{stats['unparsed']}）")
    if stats["notif"] == 0:
        print("[!] 完全沒收到通知：確認耳機沒被手機/官方 App 佔住 BLE 連線；"
              "可用 --raw 觀察原始位元組，或 --wnr 交叉測試寫入類型。")
    print_report(results)
    return 0 if results.get("headset") else 1


async def scan_only() -> int:
    from bleak import BleakScanner
    print("[*] 掃描 BLE 裝置（8 秒）…")
    devs = await BleakScanner.discover(timeout=8.0, return_adv=True)
    if not devs:
        print("[-] 沒找到任何裝置（確認藍牙已開、且已安裝 bleak 相依）。")
        return 1
    for dev, adv in sorted(devs.values(), key=lambda x: (x[0].name or "~")):
        name = adv.local_name or dev.name or "(無名稱)"
        print(f"  {dev.address:<20} rssi={getattr(adv, 'rssi', '?'):>4}  {name}")
    return 0


# ------------------------------------------------------------------ 離線自測
# 以下為實機錄到的封包（見 shokz-ble-protocol-analysis.md §9.1）
CAPTURED_EARS_GET = ("A5 5A 2C 00 01 01 00 00 28 00 A7 74 01 00 00 00 20 00 00 00 "
                     "02 00 00 00 04 00 00 00 02 00 00 00 03 00 00 00 04 00 00 00 "
                     "03 00 00 00 04 00 00 00 00 00 00 00")
CAPTURED_CASE_GET = ("A5 5A 2C 00 01 01 00 00 28 00 46 AB 01 00 00 00 20 00 00 00 "
                     "02 00 00 00 04 00 00 00 02 00 00 00 03 00 00 00 04 00 00 00 "
                     "0B 00 00 00 04 00 00 00 00 00 00 00")
CAPTURED_EARS_RESP = ("A5 5A 30 00 01 01 00 00 2C 00 7A AE 01 00 00 00 24 00 00 00 "
                      "02 00 00 00 04 00 00 00 02 00 00 00 03 00 00 00 04 00 00 00 "
                      "03 80 00 00 04 00 00 00 04 00 00 00 00 09 09 00")
CAPTURED_CASE_RESP = ("A5 5A 30 00 01 01 00 00 2C 00 AB 2B 01 00 00 00 24 00 00 00 "
                      "02 00 00 00 04 00 00 00 02 00 00 00 03 00 00 00 04 00 00 00 "
                      "0B 80 00 00 04 00 00 00 04 00 00 00 00 50 00 00")

# EQ：GET 請求（42 AF）與五種模式的回應（皆為實機捕獲，見 §9.1）
CAPTURED_EQ_GET = ("A5 5A 2C 00 01 01 00 00 28 00 42 AF 01 00 00 00 20 00 00 00 "
                   "02 00 00 00 04 00 00 00 02 00 00 00 03 00 00 00 04 00 00 00 "
                   "08 00 00 00 04 00 00 00 00 00 00 00")
_EQ_RESP_MID = ("01 00 00 00 28 00 00 00 02 00 00 00 04 00 00 00 02 00 00 00 "
                "03 00 00 00 04 00 00 00 08 80 00 00 04 00 00 00 08 00 00 00 ")
CAPTURED_EQ_RESPONSES = [  # (App 模式, 回包 CRC, payload, mode, bands)
    ("一般模式", "B2 18", "00 01 00 00 00 00 00 00", 1, [0, 0, 0, 0, 0]),
    ("人聲模式", "AC 37", "00 02 FA FE 04 05 01 00", 2, [-6, -2, 4, 5, 1]),
    ("低音增強", "0D 55", "00 03 09 F9 00 00 00 00", 3, [9, -7, 0, 0, 0]),
    ("隱私保護", "A4 75", "00 04 00 00 01 04 05 00", 4, [0, 0, 1, 4, 5]),
    ("自訂 EQ(65Hz=+5)", "F7 8D", "00 05 05 00 00 00 00 00", 5, [5, 0, 0, 0, 0]),
    ("自訂 EQ(五滑桿)", "4C 46", "00 05 FB 05 FB 05 FB 00", 5, [-5, 5, -5, 5, -5]),
]


def _hx(s: str) -> bytes:
    return bytes.fromhex("".join(s.split()))


def selftest() -> int:
    checks: list[tuple[str, bool]] = []

    # 1) 建框：CRC 與實機錄到的 GET 框一致
    checks.append(("GET 耳機框 CRC 相符",
                   get_headset_frame() == _hx(CAPTURED_EARS_GET)))
    checks.append(("GET 充電盒框 CRC 相符",
                   get_case_frame() == _hx(CAPTURED_CASE_GET)))

    # 2) 解框 + 電量：耳機 100/100
    fe = parse_response(_hx(CAPTURED_EARS_RESP))
    checks.append(("耳機回應 CRC 通過", bool(fe and fe["crc_ok"])))
    be = decode_battery(fe) if fe else None
    checks.append(("耳機 tag=0x03 (遮罩 0x8003)",
                   bool(fe and fe["tag"] == GET_TAG_HEADSET)))
    checks.append(("耳機電量 = 100% / 100%",
                   bool(be and be["left_%"] == 100 and be["right_%"] == 100)))

    # 3) 解框 + 電量：充電盒 80
    fc = parse_response(_hx(CAPTURED_CASE_RESP))
    checks.append(("充電盒回應 CRC 通過", bool(fc and fc["crc_ok"])))
    bc = decode_battery(fc) if fc else None
    checks.append(("充電盒 tag=0x0B (遮罩 0x800B)",
                   bool(fc and fc["tag"] == GET_TAG_CASE)))
    checks.append(("充電盒電量 = 80%",
                   bool(bc and bc["case_%"] == 80)))

    # 4) raw→% 公式
    checks.append(("raw 公式 0..9 / 10..100 / >100",
                   (raw_to_percent(0), raw_to_percent(9), raw_to_percent(50),
                    raw_to_percent(200)) == (10, 100, 50, 200)))

    # 5) EQ：請求框 CRC 與實機一致
    checks.append(("GET EQ 框 CRC 相符（42 AF）",
                   get_eq_frame() == _hx(CAPTURED_EQ_GET)))

    # 6) EQ：五種模式回應解析（模式名、五帶、保留 byte、CRC）
    eq_results: list[str] = []
    for app_name, crc, payload, exp_mode, exp_bands in CAPTURED_EQ_RESPONSES:
        raw = _hx(f"A5 5A 34 00 01 01 00 00 30 00 {crc} " + _EQ_RESP_MID + payload)
        fr = parse_response(raw)
        de = decode_eq(fr) if fr else None
        passed = bool(fr and fr["crc_ok"] and de and de["mode"] == exp_mode
                      and de["bands"] == exp_bands and de["reserved"] == 0
                      and de["mode_name"] == EQ_MODE_NAMES[exp_mode])
        checks.append((f"EQ {app_name} → mode={exp_mode}、帶={exp_bands}、保留=0", passed))
        if de:
            eq_results.append(f"  {app_name:22s} → {de['mode_name']}"
                              f"（band: {', '.join(f'{v:+d}' for v in de['bands'])}）")

    ok = all(c[1] for c in checks)
    print("========== shokz_battery_poc 離線自測 ==========")
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print("===============================================")
    if be:
        print(f"  解出：耳機 左 {be['left_%']}% / 右 {be['right_%']}%")
    if bc:
        print(f"  解出：充電盒 {bc['case_%']}%")
    if eq_results:
        print("  解出：EQ 模式")
        print("\n".join(eq_results))
    print("\n結果：", "全部通過 ✅" if ok else "有測試失敗 ❌")
    return 0 if ok else 1


# ------------------------------------------------------------------ CLI
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Shokz 電量檢驗 POC（BLE / A5 5A over FEF2→FEF1）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", default="Shokz",
                    help='裝置名稱關鍵字、MAC，或 macOS UUID（預設 "Shokz"）')
    ap.add_argument("--scan", action="store_true", help="只掃描並列出附近 BLE 裝置")
    ap.add_argument("--selftest", action="store_true", help="離線驗證解析邏輯（不需藍牙）")
    ap.add_argument("--raw", action="store_true", help="傾印所有通知的原始 hex")
    ap.add_argument("--eq", action="store_true", help="同時查詢 EQ 模式與五帶數值（GET 0x08）")
    ap.add_argument("--wnr", action="store_true",
                    help="改以 Write Without Response 優先（預設 Write Request，與官方 App 一致）")
    ap.add_argument("-t", "--timeout", type=float, default=3.0,
                    help="每個指令等待回應的秒數（預設 3）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    try:
        import bleak  # noqa: F401
    except ImportError:
        print("[-] 需要 bleak：pip install bleak（或用 --selftest 離線驗證）", file=sys.stderr)
        return 3
    if args.scan:
        return asyncio.run(scan_only())
    return asyncio.run(read_once(args.target, args.raw, args.timeout, args.eq, args.wnr))


if __name__ == "__main__":
    raise SystemExit(main())