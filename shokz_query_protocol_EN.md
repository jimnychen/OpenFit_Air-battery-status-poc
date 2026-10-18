# Shokz A5 5A Command Packet Generator

## Overview

This script generates a generic Shokz A5 5A command packet. It accepts a `cmdType` (for example, `GET = 2`) and a `cmdId`, then outputs the complete packet as HEX. The result can be pasted directly into nRF Connect and written to `FEF2` (`0000FEF2-0000-1000-8000-00805F9B34FB`). At the same time, the script subscribes to `FEF1` to receive the response.

## Packet Format

The packet format below has been verified against real-device captures byte-by-byte.

### Header (12 bytes)

- `A5 5A` — magic
- `u16LE n + 0x2C` — total frame length (`= total length - 8`)
- `01 01 00 00` — `[6] = protocolType` (`0 = command`)
- `u16LE n + 0x28` — TLV length
- `u16LE CRC-16/MAXIM(TLV)` — CRC of the TLV payload (little-endian)

### TLV Structure

Each field is a `u32LE` tag or length.

```text
01 00 00 00  (0x20+n) 00 00 00       ← tag1, contains length = 0x20+n
  02 00 00 00  04 00 00 00  cmdType 00 00 00    ← tag2 = cmdType (GET=2)
  03 00 00 00  04 00 00 00  cmdId   00 00 00    ← tag3 = cmdId
  04 00 00 00  n  00 00 00  value[n]            ← tag4 = value (empty for GET)
```

## Example Usage

```bash
python3 make_shokz_query.py --cmd 2 --cmdid 0x08        # EQ
python3 make_shokz_query.py --cmd 2 --cmdid 0x03        # earbud battery
python3 make_shokz_query.py --cmd 2 --cmdid 0x0B        # charging case battery
python3 make_shokz_query.py --cmd 2 --cmdid 0x02        # version
python3 make_shokz_query.py --cmd 2 --cmdid 0x08 --single   # single-line output
python3 make_shokz_query.py --selftest                  # compare against real-device verification packets
```

## Notes

This script is intended for protocol analysis and verification. It is useful when validating a raw frame against actual hardware behavior or when constructing a frame to test the Bluetooth LE command path against the Shokz device.
