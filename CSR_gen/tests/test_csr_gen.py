import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from subprocess import run
from sys import executable
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook

import csr_gen


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "outputs" / "csr"


def workbook(interface="APB", end="0x1000"):
    book = Workbook()
    description = book.active
    description.title = "Description"
    description.append(("sys name", "test"))
    description.append(("Version", csr_gen.VERSION))
    description.append(csr_gen.BLOCK_HEADERS)
    description.append(("block", "REG", "0x0", end, "Registers", interface))
    book.create_sheet("Registers").append(csr_gen.HEADERS)
    return book


def register(sheet, name, offset, access="RW", reset="0x0", secure="non-secure"):
    sheet.append((name, hex(offset), "value", "[31:0]", access, reset, secure, "NO"))


def plan(book, data_width=32, addr_width=32, out=Path("build")):
    return csr_gen.plan_outputs(book, Namespace(out=out, data_width=data_width, addr_width=addr_width))


class WorkbookTests(unittest.TestCase):
    def test_published_template_and_example_rtl(self):
        with (EXAMPLES / "csrgen_template.xlsx").open("rb") as source:
            book = load_workbook(source)
        present = {row[4] for sheet in (book["Register"], book["Interrupts"])
                   for row in sheet.iter_rows(min_row=2, values_only=True) if row[4]}
        self.assertEqual(present, set(csr_gen.ACCESS))
        outputs = plan(book, out=EXAMPLES)
        self.assertEqual(len(outputs), 2)
        for path, rtl in outputs:
            self.assertEqual(path.read_text(encoding="utf-8"), rtl)

    def test_mixed_case_literals_merges_and_last_address(self):
        book = workbook("rEgBuS")
        book["Description"]["E4"] = " registers "
        sheet = book["Registers"]
        sheet.append(("ctrl", "32’H0000_0FFC", "upper", "[31:16]", " RW ", "16'hABcd", "NON-SECURE", "NO"))
        sheet.append((None, None, "lower", "[15:0]", "rW", "0X0001"))
        sheet.merge_cells("A2:A3")
        rtl = plan(book)[0][1]
        self.assertIn("12'hffc", rtl)
        self.assertIn("16'habcd", rtl)
        sheet["B2"] = "0x1000"
        with self.assertRaisesRegex(ValueError, "entire word"):
            plan(book)

    def test_window_errors_and_memory_overlaps(self):
        book = workbook("regbus", "0xC00")
        register(book["Registers"], "a", 0)
        with self.assertRaisesRegex(ValueError, "power-of-two"):
            plan(book)
        book["Description"]["F4"] = "APB"
        self.assertEqual(len(plan(book)), 1)
        book["Description"].append(("ram", "MEMORY", "0x800", "0xC00", None, "regbus"))
        with self.assertRaisesRegex(ValueError, "overlapping blocks"):
            plan(book)
        with self.assertRaisesRegex(ValueError, "within address width"):
            plan(book, addr_width=11)

    def test_address_word_must_fit_even_with_non_power_of_two_bus_bytes(self):
        book = workbook("regbus", "0x400")
        register(book["Registers"], "a", 0x3F0)
        self.assertRegex(plan(book, data_width=96)[0][1], r"input\s+logic\s+\[95:0\]\s+s_regbus_req_wdata")
        book["Registers"]["B2"] = "0x3FC"
        with self.assertRaisesRegex(ValueError, "entire word"):
            plan(book, data_width=96)

    def test_zero_offset_preserves_byte_address_bits(self):
        for interface, width, port, msb in (
            ("APB", 32, "paddr", 1), ("regbus", 32, "s_regbus_req_addr", 1),
            ("regbus", 96, "s_regbus_req_addr", 3),
        ):
            with self.subTest(interface=interface, width=width):
                book = workbook(interface)
                register(book["Registers"], "a", 0)
                self.assertIn(f"assign csr_addr = {port}[{msb}:0];", plan(book, data_width=width)[0][1])

    def test_real_block_after_guidelines_is_reported(self):
        book = workbook()
        register(book["Registers"], "a", 0)
        book["Description"].append((csr_gen.ACCESS_GUIDELINES_HEADING,))
        book["Description"].append(("Note", None, "Description of the guideline."))
        self.assertEqual(len(plan(book)), 1)
        book["Description"].append(("ram", "MEMORY", "0x1000", "0x1400", None, "APB"))
        with self.assertRaisesRegex(ValueError, "above.*Access Usage Guidelines"):
            plan(book)

    def test_field_cannot_collide_with_write_once_state(self):
        book = workbook()
        sheet = book["Registers"]
        sheet.append(("a", "0x0", "status", "[31:16]", "W1", "0x0", "non-secure", "NO"))
        sheet.append(("a", "0x0", "status_written", "[15:0]", "RO", None, "non-secure", "NO"))
        with self.assertRaisesRegex(ValueError, "duplicate generated signal name block_a_status_written"):
            plan(book)

    def test_block_prefixed_scalar_ports_and_bit_selects(self):
        book = workbook()
        sheet = book["Registers"]
        sheet.append(("ctrl", "0x0", "reserve", "[31:2]", None, None, "non-secure", "NO"))
        sheet.append(("ctrl", "0x0", "status", "[1]", "W1C", "0x0", "non-secure", "NO"))
        sheet.append(("ctrl", "0x0", "enable", "[0]", "W1", "0x0", "non-secure", "NO"))
        rtl = plan(book)[0][1]
        port_lines = [line for line in rtl.splitlines() if line.lstrip().startswith(("input ", "output "))]
        positions = {len(line) - len(line.rstrip(",").split()[-1]) - int(line.endswith(",")) for line in port_lines}
        self.assertEqual(len(positions), 1, port_lines)
        self.assertRegex(rtl, r"output logic\s+block_ctrl_enable\b")
        self.assertRegex(rtl, r"output logic\s+block_ctrl_status\b")
        self.assertNotIn("_hw_set", rtl)
        self.assertIn("read_data[0] = block_ctrl_enable;", rtl)
        self.assertIn("csr_wdata[1]", rtl)
        self.assertNotIn("[0:0]", rtl)
        self.assertIn("logic block_ctrl_enable_written", rtl)

    def test_field_name_cannot_collide_with_regbus_port(self):
        book = workbook("regbus")
        book["Description"]["A4"] = "s"
        book["Registers"].append(("regbus", "0x0", "req_addr", "[31:0]", "RO", None, "non-secure", "NO"))
        with self.assertRaisesRegex(ValueError, "duplicate generated signal name s_regbus_req_addr"):
            plan(book)

    def test_interrupt_windows_and_ignored_columns(self):
        book = workbook(end="0x400")
        sheet = book["Registers"]
        sheet.append(("event", "0x60", "ignored", "ignored", "ignored", "ignored", "ignored", "YES"))
        rtl = plan(book)[0][1]
        self.assertIn("paddr[9:0]", rtl)
        register(sheet, "next", 0x25C)
        with self.assertRaisesRegex(ValueError, "at least 0x260"):
            plan(book)
        sheet["B3"] = "0x260"
        self.assertIn("10'h260", plan(book)[0][1])
        sheet["B2"] = "0x204"
        with self.assertRaisesRegex(ValueError, "0x200 interrupt window"):
            plan(book)

    def test_large_map_with_adjacent_macros_and_registers(self):
        book = workbook(end="0x8000")
        sheet = book["Registers"]
        for index in range(16):
            sheet.append((f"irq{index}", hex(index * 0x200), None, None, None, None, None, "YES"))
        for index in range(1024):
            register(sheet, f"reg{index}", 0x2000 + index * 4)
        rtl = plan(book)[0][1]
        self.assertEqual(rtl.count("always_ff"), 16 * 4 + 1024)
        self.assertIn("block_irq15_intr_info[31][63:32]", rtl)
        self.assertEqual(plan(book)[0][1], rtl)

    def test_invalid_xlsx_reports_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "invalid.xlsx"
            with ZipFile(path, "w") as archive:
                archive.writestr("unrelated.xml", "<root/>")
            with self.assertRaisesRegex(ValueError, "invalid Excel workbook"):
                csr_gen.load_register_workbook(path)

    def test_cli_validates_all_blocks_before_creating_output(self):
        book = workbook("regbus", "0x400")
        register(book["Registers"], "a", 0)
        book["Description"].append(("second", "REG", "0x400", "0x800", "Other", "APB"))
        other = book.create_sheet("Other")
        other.append(csr_gen.HEADERS)
        register(other, "b", 0x3FC)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.xlsx"
            output = root / "rtl"
            command = [executable, "-B", str(ROOT / "csr_gen.py"), str(source), "--out", str(output)]
            for invalid_cell, value, message in (
                (book["Description"]["B2"], "r9p9", "Version mismatch"),
                (other["B2"], "0x400", "entire word inside the block"),
            ):
                old_value = invalid_cell.value
                invalid_cell.value = value
                book.save(source)
                result = run(command, cwd=root, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
                self.assertFalse(output.exists())
                invalid_cell.value = old_value
            book.save(source)
            result = run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({file.name for file in output.glob("*.sv")},
                             {"test_block_csr_reg.sv", "test_second_csr_reg.sv"})


if __name__ == "__main__":
    unittest.main()
