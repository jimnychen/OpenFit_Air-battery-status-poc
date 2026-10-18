from __future__ import annotations

import argparse
import struct
import sys

CMD_GET = 2  # cmdType：1=SET, 2=GET, 3=SYNC(通知)


def crc16_maxim(data: bytes) -> int:
    """CRC-16/MAXIM-DOW（poly 0x8005 反射、init 0、xorout 0xFFFF）。"""
    crc = 0x0000
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return (crc ^ 0xFFFF) & 0xFFFF


def build_frame(cmd_type: int, cmd_id: int, value: bytes = b"") -> bytes:
    """組出一個 A5 5A 指令框（value 留空即 GET 查詢）。"""
    u16 = lambda v: struct.pack("<H", v & 0xFFFF)
    u32 = lambda v: struct.pack("<I", v & 0xFFFFFFFF)
    n = len(value)
    tlv = b"".join([u32(1), u32(n + 0x20),          # tag1: 容器, 長度 0x20+n
                    u32(2), u32(4), u32(cmd_type),  # tag2: cmdType
                    u32(3), u32(4), u32(cmd_id),    # tag3: cmdId
                    u32(4), u32(n)]) + value        # tag4: value（GET 空）
    return (b"\xA5\x5A" + u16(n + 0x2C) + b"\x01\x01\x00\x00" +
            u16(n + 0x28) + u16(crc16_maxim(tlv)) + tlv)


def format_hex(data: bytes, single: bool = False, line_len: int = 16) -> str:
    if single:
        return " ".join(f"{b:02X}" for b in data)
    return "\n".join(" ".join(f"{b:02X}" for b in data[i:i + line_len])
                     for i in range(0, len(data), line_len))


# ---------------------------------------------------------------- 自我驗證
# 以下三筆為實機捕獲的封包原文，用來確認本檔產生的封包與實機送出的完全一致：
#   - ears/case：nRF Connect 實送時錄下（shokz-ble-protocol-analysis.md §9.1）；
#   - EQ：官方 App 經 RFCOMM 實際送出的 GET 0x08（btsnoop 捕獲）。
# 註：byte[5] 是傳輸通道旗標（BLE-ATT 觀察到 0x01、RFCOMM 觀察到 0x00），
#     內容與 CRC 相同；比對 EQ 向量時把該 byte 正規化。
EARS_VERIFIED = bytes.fromhex("""
    A5 5A 2C 00 01 01 00 00 28 00 A7 74
    01 00 00 00 20 00 00 00 02 00 00 00 04 00 00 00 02 00 00 00
    03 00 00 00 04 00 00 00 03 00 00 00 04 00 00 00 00 00 00 00
""")
CASE_VERIFIED = bytes.fromhex("""
    A5 5A 2C 00 01 01 00 00 28 00 46 AB
    01 00 00 00 20 00 00 00 02 00 00 00 04 00 00 00 02 00 00 00
    03 00 00 00 04 00 00 00 0B 00 00 00 04 00 00 00 00 00 00 00
""")
EQ_CAPTURED_RFCOMM = bytes.fromhex("""
    A5 5A 2C 00 01 00 00 00 28 00 42 AF
    01 00 00 00 20 00 00 00 02 00 00 00 04 00 00 00 02 00 00 00
    03 00 00 00 04 00 00 00 08 00 00 00 04 00 00 00 00 00 00 00
""")


def _normalize_transport_flag(frame: bytes) -> bytes:
    """把 byte[5]（傳輸旗標）歸零後再用於比對。"""
    return frame[:5] + b"\x00" + frame[6:]


def selftest() -> int:
    ok = True
    cases = (
        ("ears GET (cmdId=3)", build_frame(2, 0x03), EARS_VERIFIED, False),
        ("case GET (cmdId=0x0B)", build_frame(2, 0x0B), CASE_VERIFIED, False),
        ("EQ   GET (cmdId=8, 對照 App RFCOMM 實送)",
         build_frame(2, 0x08), EQ_CAPTURED_RFCOMM, True),
    )
    for label, frame, expect, normalize in cases:
        a, b = (map(_normalize_transport_flag, (frame, expect)) if normalize else (frame, expect))
        match = a == b
        ok &= match
        print(f"[{'PASS' if match else 'FAIL'}] {label}: {format_hex(frame, single=True)}")
    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Shokz A5 5A 指令封包產生器（輸出可直接貼 nRF Connect）")
    p.add_argument("--cmd", "--getid", dest="cmd", type=lambda x: int(x, 0), default=CMD_GET,
                   help="cmdType：1=SET, 2=GET（預設 2）")
    p.add_argument("--cmdid", type=lambda x: int(x, 0), default=None,
                   help="cmdId（如 0x03 耳機電量, 0x08 EQ, 0x0B 充電盒電量）")
    p.add_argument("--value", default="",
                   help="（選用）value 的 HEX 字串，用於 SET 指令，如 '01 02'")
    p.add_argument("--single", action="store_true", help="單行 HEX 輸出")
    p.add_argument("--selftest", action="store_true", help="對照兩筆實機驗證封包")
    args = p.parse_args()

    if args.selftest:
        return selftest()
    if args.cmdid is None:
        p.error("需要 --cmdid（或使用 --selftest）")

    value = bytes.fromhex(args.value.replace(" ", "")) if args.value else b""
    frame = build_frame(args.cmd, args.cmdid, value)

    print(f"# cmdType={args.cmd} cmdId=0x{args.cmdid:02X} valueLen={len(value)} total={len(frame)}B")
    print("# 寫入 FEF2 (0000FEF2-0000-1000-8000-00805F9B34FB)，回應從 FEF1 通知")
    print(format_hex(frame, single=args.single))
    return 0


if __name__ == "__main__":
    sys.exit(main())