"""Generate synthesizable regbus or APB4 CSR slaves from Excel."""

import argparse
import re
from pathlib import Path
from zipfile import BadZipFile

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException


# Each entry defines the read and write action for one field.
ACCESS = {
    "RO": ("read", None),
    "RW": ("read", "replace"),
    "RC": ("clear", None),
    "RS": ("set", None),
    "WRC": ("clear", "replace"),
    "WRS": ("set", "replace"),
    "WC": ("read", "clear"),
    "WS": ("read", "set"),
    "WSRC": ("clear", "set"),
    "WCRS": ("set", "clear"),
    "W1C": ("read", "one_clear"),
    "W0C": ("read", "zero_clear"),
    "W1S": ("read", "one_set"),
    "W0S": ("read", "zero_set"),
    "W1P": ("zero", "pulse"),
    "W1T": ("read", "one_toggle"),
    "W0T": ("read", "zero_toggle"),
    "W1SRC": ("clear", "one_set"),
    "W0SRC": ("clear", "zero_set"),
    "W1CRS": ("set", "one_clear"),
    "W0CRS": ("set", "zero_clear"),
    "WO": ("error", "replace"),
    "WOC": ("error", "clear"),
    "WOS": ("error", "set"),
    "W1": ("read", "once"),
    "WO1": ("error", "once"),
}
REGBUS_PORTS = {
    "s_regbus_req_valid", "s_regbus_req_ready", "s_regbus_req_write",
    "s_regbus_req_addr", "s_regbus_req_wdata", "s_regbus_ack_valid",
    "s_regbus_ack_ready", "s_regbus_ack_rdata",
}
RTL_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent / "templates"),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
HEADERS = ("Register", "Offset", "Field", "Bits", "Access", "Reset", "Secure Type", "Interrupt")
INTERRUPT_WINDOW = 0x200
BLOCK_HEADERS = ("Block", "Type", "START", "END", "Sheet", "Interface")
ACCESS_GUIDELINES_HEADING = "Access Usage Guidelines"
VERSION = "r1p0"
HEX_DIGITS = r"[0-9a-f]+(?:_[0-9a-f]+)*"
HEX_LITERAL = re.compile(
    rf"(?:0x|(?P<width>[0-9]+)['‘’]h)(?P<digits>{HEX_DIGITS})",
    re.IGNORECASE | re.ASCII,
)


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value.strip()):
        raise ValueError(f"invalid identifier {value!r}")
    return value.strip().lower()


def hex_cell(value, column):
    match = HEX_LITERAL.fullmatch(value.strip()) if isinstance(value, str) else None
    if not match:
        raise ValueError(f"{column}: expected hexadecimal text such as 0xFC or 32'hFC, got {value!r}")
    width, digits = match.group("width", "digits")
    result = int(digits, 16)
    if width is not None:
        width = int(width)
        if width == 0 or result.bit_length() > width:
            raise ValueError(f"{column}: {value!r} does not fit its declared literal width")
    return result


def label(value):
    return value.strip().casefold() if isinstance(value, str) else value


def sheet_rows(sheet, headers, header_row=1):
    rows = sheet.iter_rows(min_row=header_row, max_col=len(headers), values_only=True)
    actual = next(rows, ())
    if tuple(map(label, actual)) != tuple(map(label, headers)):
        raise ValueError(f"{sheet.title} row {header_row} must be: {', '.join(headers)}")
    return enumerate(rows, header_row + 1)


def read_system(sheet):
    for row, expected in enumerate(("sys name", "Version"), 1):
        if label(sheet.cell(row, 1).value) != label(expected):
            raise ValueError(f"Description row {row}: expected {expected!r} in column A")
    version = sheet["B2"].value
    if version != VERSION:
        raise ValueError(f"Description row 2: Version mismatch (workbook {version!r}, script {VERSION!r})")
    try:
        return name(sheet["B1"].value)
    except ValueError as exc:
        raise ValueError(f"Description row 1: invalid sys name ({exc})") from exc


def read_blocks(description, sheet_names, addr_width):
    blocks = []
    names = set()
    sheets = set()
    in_guidelines = False
    for row_num, row in sheet_rows(description, BLOCK_HEADERS, header_row=3):
        if all(value is None for value in row):
            continue
        if label(row[0]) == label(ACCESS_GUIDELINES_HEADING) and all(value is None for value in row[1:]):
            in_guidelines = True
            continue
        if in_guidelines:
            if label(row[1]) in ("reg", "memory") or label(row[5]) in ("apb", "regbus"):
                raise ValueError(f"Description row {row_num}: move the block above {ACCESS_GUIDELINES_HEADING}")
            continue
        try:
            block, kind, start, end, sheet, interface = row
            block = name(block)
            kind = str(kind).strip().upper()
            start, end = hex_cell(start, "START"), hex_cell(end, "END")
            size = end - start
            if block in names:
                raise ValueError("duplicate block name")
            if kind not in ("REG", "MEMORY"):
                raise ValueError("Type must be REG or MEMORY")
            if not 0 <= start < end <= 1 << addr_width or start % 1024 or size % 1024:
                raise ValueError("require 0 <= START < END within address width and 1 KiB alignment of START and length")
            interface = str(interface).strip().lower()
            if interface not in ("apb", "regbus"):
                raise ValueError("Interface must be APB or regbus")
            if interface == "regbus" and (size & (size - 1) or start % size):
                raise ValueError("regbus master requires a power-of-two window and START aligned to its size")
            if kind == "REG":
                actual_sheet = sheet_names.get(label(sheet)) if isinstance(sheet, str) else None
                if actual_sheet is None or actual_sheet == description.title:
                    raise ValueError(f"missing register sheet {sheet!r}")
                sheet = actual_sheet
                if sheet in sheets:
                    raise ValueError(f"register sheet {sheet!r} already assigned")
                sheets.add(sheet)
            names.add(block)
            blocks.append((start, end, block, kind, sheet, row_num, interface))
        except ValueError as exc:
            raise ValueError(f"Description row {row_num}: {exc}") from exc
    if not blocks:
        raise ValueError("Description has no blocks")
    blocks.sort()
    for previous, current in zip(blocks, blocks[1:]):
        if current[0] < previous[1]:
            raise ValueError(f"Description rows {previous[5]} and {current[5]}: overlapping blocks")
    return blocks


def merged_anchors(sheet, columns):
    anchors = {}
    for area in sheet.merged_cells.ranges:
        if not any(area.min_col <= col <= area.max_col for col in columns):
            continue
        if area.min_col != area.max_col or area.min_row < 2:
            raise ValueError(f"{sheet.title}: invalid register metadata merge {area.coord}")
        for row in range(area.min_row + 1, area.max_row + 1):
            anchors[row, area.min_col] = area.min_row
    return anchors


def register_metadata(sheet, anchors, row, column, register_anchor, register):
    anchor = anchors.get((row, column), register_anchor)
    owner = anchors.get((anchor, 1), anchor)
    if name(sheet.cell(owner, 1).value) != name(register):
        raise ValueError(f"column {column} merge crosses register boundaries")
    return sheet.cell(anchor, column).value


def bit_range(value):
    match = re.fullmatch(r"\[\s*([0-9]+)\s*(?::\s*([0-9]+)\s*)?\]", value.strip()) if isinstance(value, str) else None
    if not match:
        raise ValueError("Bits must use [A:B] or [X], e.g. [31:9] or [8]")
    high, low = match.groups()
    msb, lsb = int(high), int(low or high)
    if not 0 <= lsb <= msb <= 31:
        raise ValueError("Bits must satisfy 0 <= B <= A <= 31")
    return msb, lsb


def add_interrupt(register, offset, reserved_stems, packed_ports, packed_reads):
    prefix = f"{register}_intr"
    registers = []

    def add_word(stem, address, access):
        if stem in reserved_stems:
            raise ValueError(f"duplicate interrupt or field signal name {stem}")
        reserved_stems.add(stem)
        registers.append((address, "write", [(stem, 31, 0, access, 0)]))

    names = ("enable", "mask", "clear", "inject", "status_raw", "status_masked")
    for word, suffix in enumerate(names):
        add_word(f"{prefix}_{suffix}", offset + 4 * word, "RW" if word < 4 else "RO")
    address = offset + 4 * len(names)
    for suffix, width in (("counter", 32), ("info", 64)):
        array = f"{prefix}_{suffix}"
        if array in reserved_stems:
            raise ValueError(f"duplicate interrupt or field signal name {array}")
        reserved_stems.add(array)
        packed_ports[array] = width
        for source in range(32):
            for low in range(0, width, 32):
                stem = f"{array}_{source}_{low}"
                add_word(stem, address, "RO")
                packed_reads[stem] = (array, source, low, width)
                address += 4
    return registers


def read_fields(sheet, block, data_width, start, end, *, apb=False):
    registers = {}
    offsets = set()
    stems = set() if apb else set(REGBUS_PORTS)
    anchors = merged_anchors(sheet, (1, 2, 7, 8))
    interrupts = set()
    expanded = []
    packed_ports = {}
    packed_reads = {}
    interrupt_end = 0
    previous_reg = None
    next_bit = -1
    for row_num, row in sheet_rows(sheet, HEADERS):
        if all(value is None for value in row):
            continue
        try:
            reg, offset, field, bits, access, reset, security, interrupt = row
            reg_anchor = anchors.get((row_num, 1), row_num)
            if reg_anchor != row_num:
                reg = sheet.cell(reg_anchor, 1).value
            if offset is None:
                offset = register_metadata(sheet, anchors, row_num, 2, reg_anchor, reg)
            if interrupt is None:
                interrupt = register_metadata(sheet, anchors, row_num, 8, reg_anchor, reg)
            interrupt = label(interrupt)
            if interrupt not in ("yes", "no"):
                raise ValueError("Interrupt must be YES or NO")
            reg = name(reg)
            offset = hex_cell(offset, "Offset")
            word_bytes = data_width // 8
            if offset % word_bytes or offset + word_bytes > end - start:
                raise ValueError("offset must be word-aligned and the entire word inside the block")
            if reg in interrupts:
                raise ValueError(f"interrupt macro {reg} must occupy exactly one row")
            if offset < interrupt_end:
                raise ValueError(f"offset after interrupt macro must be at least 0x{interrupt_end:X}")
            if interrupt == "yes":
                if not apb:
                    raise ValueError("interrupt macros require an APB block")
                if next_bit != -1:
                    raise ValueError(f"{previous_reg} must cover every bit down to [0]")
                if reg in registers:
                    raise ValueError(f"duplicate register or interrupt macro name {reg}")
                interrupt_end = offset + INTERRUPT_WINDOW
                if interrupt_end > end - start:
                    raise ValueError("the full 0x200 interrupt window must fit inside the block")
                if any(offset <= previous < interrupt_end for previous in offsets):
                    raise ValueError("interrupt window overlaps a previous register")
                interrupts.add(reg)
                expanded.extend(add_interrupt(f"{block}_{reg}", offset, stems, packed_ports, packed_reads))
                previous_reg = None
                continue
            msb, lsb = bit_range(bits)
            if security is None:
                security = register_metadata(sheet, anchors, row_num, 7, reg_anchor, reg)
            security = str(security).strip().lower()
            if security not in ("secure", "non-secure"):
                raise ValueError("Secure Type must be secure or non-secure")
            secure = security == "secure"
            if secure and not apb:
                raise ValueError("regbus registers must use non-secure")
            field = name(field)
            stem = f"{block}_{reg}_{field}"
            if not 0 <= lsb <= msb < data_width:
                raise ValueError("bit range outside data word")
            if reg != previous_reg:
                if next_bit != -1:
                    raise ValueError(f"{previous_reg} must cover every bit down to [0]")
                if reg in registers:
                    raise ValueError(f"{reg} field rows must be grouped together")
                previous_reg = reg
                next_bit = 31
            if msb != next_bit:
                raise ValueError(f"{reg} expected field starting at bit {next_bit}; fill gaps with reserve")
            next_bit = lsb - 1
            if reg not in registers:
                if offset in offsets:
                    raise ValueError("offset already assigned to another register")
                registers[reg] = (offset, secure, [])
                offsets.add(offset)
            expected_offset, expected_secure, fields = registers[reg]
            if offset != expected_offset:
                raise ValueError("register offset differs from its previous row")
            if secure != expected_secure:
                raise ValueError("Secure Type differs from the previous row of this register")
            if field == "reserve":
                if access is not None or reset is not None:
                    raise ValueError("reserve fields must leave Access and Reset blank")
                continue
            access = str(access).strip().upper()
            if access not in ACCESS:
                raise ValueError(f"unknown access type {access!r}")
            reset = 0 if reset is None else hex_cell(reset, "Reset")
            width = msb - lsb + 1
            if reset.bit_length() > width:
                raise ValueError(f"Reset must fit in the {width}-bit field")
            if access in ("RO", "W1P") and reset:
                raise ValueError(f"Reset for {access} must be blank or zero")
            signal_names = {stem}
            if ACCESS[access][1] == "once":
                signal_names.add(f"{stem}_written")
            duplicates = stems & signal_names
            if duplicates:
                raise ValueError(f"duplicate generated signal name {min(duplicates)}")
            if any(lsb <= old_msb and old_lsb <= msb for _, old_msb, old_lsb, _, _ in fields):
                raise ValueError("overlapping fields")
            fields.append((stem, msb, lsb, access, reset))
            stems.update(signal_names)
        except ValueError as exc:
            raise ValueError(f"{sheet.title} row {row_num}: {exc}") from exc
    if not registers and not expanded:
        raise ValueError(f"{sheet.title} worksheet has no field definitions")
    if next_bit != -1:
        raise ValueError(f"{sheet.title}: {previous_reg} must cover every bit down to [0]")
    return sorted([*registers.values(), *expanded]), packed_ports, packed_reads, interrupt_end


def generate(module, addr_width, data_width, registers, *, apb=False, packed_ports=None,
             packed_reads=None, interrupt_end=0):
    max_offset = max(registers[-1][0], interrupt_end - 1)
    packed_ports = packed_ports or {}
    packed_reads = packed_reads or {}
    return RTL_TEMPLATES.get_template("csr.sv.j2").render(
        module=module,
        addr_width=addr_width,
        data_width=data_width,
        decode_width=max((data_width // 8 - 1).bit_length(), max_offset.bit_length()),
        registers=registers,
        fields=[field for _, _, register_fields in registers for field in register_fields if field[0] not in packed_reads],
        packed_ports=packed_ports,
        packed_reads=packed_reads,
        access_modes=ACCESS,
        write_only_access={access for access, (read, _) in ACCESS.items() if read == "error"},
        apb=apb,
    )


def load_register_workbook(path):
    with path.open("rb") as source:
        try:
            return load_workbook(source)
        except (BadZipFile, InvalidFileException, KeyError, SyntaxError, ValueError) as exc:
            raise ValueError(f"invalid Excel workbook {path}: {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--addr-width", type=int, default=32)
    parser.add_argument("--data-width", type=int, default=32)
    args = parser.parse_args()
    if args.data_width < 32 or args.data_width % 32:
        parser.error("data width must be a positive multiple of 32")
    if args.addr_width < 10:
        parser.error("address width must be at least 10")
    try:
        workbook = load_register_workbook(args.workbook)
        try:
            outputs = plan_outputs(workbook, args)
        finally:
            workbook.close()
        if outputs:
            args.out.mkdir(parents=True, exist_ok=True)
        for path, rtl in outputs:
            path.write_text(rtl, encoding="utf-8")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


def plan_outputs(workbook, args):
    sheet_names = {}
    for title in workbook.sheetnames:
        key = label(title)
        if key in sheet_names:
            raise ValueError(f"ambiguous worksheet names {sheet_names[key]!r} and {title!r}")
        sheet_names[key] = title
    if "description" not in sheet_names:
        raise ValueError("missing Description worksheet with sys name and Version")
    description = workbook[sheet_names["description"]]
    system = read_system(description)
    if args.out.suffix.lower() == ".sv" and not args.out.is_dir():
        raise ValueError("--out must be a directory for the Description format")
    blocks = read_blocks(description, sheet_names, args.addr_width)
    outputs = []
    for start, end, block, kind, sheet, row_num, protocol in blocks:
        if kind == "MEMORY":
            continue
        apb = protocol == "apb"
        if apb and args.data_width != 32:
            raise ValueError(f"Description row {row_num}: APB4 requires a 32-bit data width")
        registers, packed_ports, packed_reads, interrupt_end = read_fields(
            workbook[sheet], block, args.data_width, start, end, apb=apb
        )
        module = f"{system}_{block}_csr_reg"
        path = args.out / f"{module}.sv"
        rtl = generate(module, args.addr_width, args.data_width, registers, apb=apb,
                       packed_ports=packed_ports, packed_reads=packed_reads, interrupt_end=interrupt_end)
        outputs.append((path, rtl))
    return outputs


if __name__ == "__main__":
    main()
