#!/usr/bin/env python3

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

RESP_GET = 2             # responseType == 2


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


# ------------------------------------------------------------------ 輸出
def print_report(results: dict) -> None:
    print("\n================= 電量結果 =================")
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
    print("===========================================\n")


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


async def read_once(filter_: str, raw: bool, timeout: float) -> int:
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
    events = {"headset": asyncio.Event(), "case": asyncio.Event()}

    def on_notify(_sender, data: bytearray):
        blob = bytes(data)
        if raw:
            print("  <-", blob.hex(" "))
        frame = parse_response(blob)
        if not frame:
            return
        batt = decode_battery(frame)
        if batt:
            results[batt["kind"]] = batt
            events[batt["kind"]].set()

    print(f"[*] 連線 {label} …")
    async with BleakClient(target) as client:
        try:
            await client.pair()
            print("[+] 藍牙裝置配對/交握成功")
        except Exception as e:
            print(f"[-] 配對失敗或不支援自動配對: {e}")
        print("[+] 已連線；訂閱 FEF1 通知")
        await client.start_notify(NOTIFY_CHAR, on_notify)

        for kind, label_zh, frame in (("headset", "耳機電量", get_headset_frame()),
                                      ("case", "充電盒電量", get_case_frame())):
            events[kind].clear()
            print(f"[>] 送出 GET {label_zh}（{len(frame)}B）…")
            try:
                await client.write_gatt_char(WRITE_CHAR, frame, response=False)
            except Exception as exc:                  # 少數堆疊不支援 WNR 時退回 with-response
                print(f"    [!] WNR 失敗（{exc}），改用 with-response 重送")
                await client.write_gatt_char(WRITE_CHAR, frame, response=True)
            try:
                await asyncio.wait_for(events[kind].wait(), timeout)
            except asyncio.TimeoutError:
                print(f"    [-] 等待 {label_zh} 逾時（{timeout}s）")

        try:
            await client.stop_notify(NOTIFY_CHAR)
        except Exception:
            pass

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

    ok = all(c[1] for c in checks)
    print("========== shokz_battery_poc 離線自測 ==========")
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print("===============================================")
    if be:
        print(f"  解出：耳機 左 {be['left_%']}% / 右 {be['right_%']}%")
    if bc:
        print(f"  解出：充電盒 {bc['case_%']}%")
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
    return asyncio.run(read_once(args.target, args.raw, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())