import importlib.util
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_csr_gen import EXAMPLES, plan, register, workbook


HERE = Path(__file__).resolve().parent
WORD = 0xFFFFFFFF
WRITE_ONLY = {"WO", "WOC", "WOS", "WO1"}
READ_CLEAR = {"RC", "WRC", "WSRC", "W1SRC", "W0SRC"}
READ_SET = {"RS", "WRS", "WCRS", "W1CRS", "W0CRS"}
ACCESS_TYPES = (
    "RO", "RW", "RC", "RS", "WRC", "WRS", "WC", "WS", "WSRC", "WCRS",
    "W1C", "W0C", "W1S", "W0S", "W1P", "W1T", "W0T",
    "W1SRC", "W0SRC", "W1CRS", "W0CRS", "WO", "WOC", "WOS", "W1", "WO1",
)


def write_result(access, previous, data, mask):
    if access in {"RO", "RC", "RS"}:
        return previous
    if access in {"RW", "WRC", "WRS", "WO", "W1", "WO1"}:
        return (previous & ~mask) | (data & mask)
    if access in {"WC", "WCRS", "WOC"}:
        return previous & ~mask
    if access in {"WS", "WSRC", "WOS"}:
        return previous | mask
    if access == "W1P":
        return data & mask
    selected = (data if access.startswith("W1") else ~data) & mask
    if access.endswith("T"):
        return previous ^ selected
    if access in {"W1C", "W0C", "W1CRS", "W0CRS"}:
        return previous & ~selected
    return previous | selected


def make_apb_test(directory):
    randomizer = random.Random(0xC5A)
    book = workbook()
    sheet = book["Registers"]
    ports = []
    declarations = []
    checks = []
    for index, access in enumerate(ACCESS_TYPES):
        offset = 4 * index
        stem = f"block_reg{index}_value"
        pin = stem
        secure = index % 3 == 1
        reset = 0 if access in {"RO", "W1P"} else randomizer.getrandbits(32)
        register(sheet, f"reg{index}", offset, access, hex(reset), "secure" if secure else "non-secure")
        declarations.append(f"logic [31:0] {pin};")
        ports.append(f".{pin}({pin})")
        if access == "RO":
            declarations.append(f"assign {pin} = 32'h2521C5A3;")
        for sequence in range(2):
            if sequence:
                checks.append("reset_dut();")
            value = 0x2521C5A3 if access == "RO" else reset
            written = False
            for strobe in range(16):
                # Alternate the polarity and security of writes, with an intervening read.
                for write in (True, False):
                    data = randomizer.getrandbits(32)
                    nonsecure = bool((strobe + sequence) % 3 == 0)
                    mask = sum(0xFF << (8 * byte) for byte in range(4) if strobe & (1 << byte))
                    denied = secure and nonsecure or not write and access in WRITE_ONLY
                    read_data = 0 if denied or access == "W1P" else value
                    expected_read = f"32'h{read_data:08x}" if not write else "'0"
                    checks.append(
                        f"xfer(32'h{offset:x}, 1'b{int(write)}, 32'h{data:08x}, "
                        f"4'h{strobe if write else 0:x}, 1'b{int(nonsecure)}, 1'b{int(denied)}, {expected_read});"
                    )
                    if access == "W1P":
                        value = 0
                    if not denied:
                        if write and not written:
                            value = write_result(access, value, data, mask)
                            written = access in {"W1", "WO1"} and bool(mask)
                        elif not write and access in READ_CLEAR | READ_SET:
                            value = WORD if access in READ_SET else 0
                    if access != "RO":
                        checks.append(f"if ({pin} !== 32'h{value:08x}) $fatal(1, \"{access} state\");")
            checks.append("reset_dut();")

    sheet.append(("mixed", "0x100", "state", "[31:16]", "RC", "0xABCD", "non-secure", "NO"))
    sheet.append(("mixed", "0x100", "command", "[15:0]", "WO", "0x0", "non-secure", "NO"))
    checks.extend([
        "xfer(32'h100, 1'b0, '0, '0, 1'b0, 1'b1, '0);",
        "if (dut.block_mixed_state !== 16'hABCD) $fatal(1, \"rejected read caused RC\");",
    ])
    sheet.append(("event", "0x400", None, None, None, None, None, "YES"))
    declarations.extend([
        "logic [31:0][31:0] counters;",
        "logic [31:0][63:0] infos;",
    ])
    ports.extend([
        ".block_event_intr_counter(counters)",
        ".block_event_intr_info(infos)",
        ".block_event_intr_status_raw(32'hAB321001)",
        ".block_event_intr_status_masked(32'hFF012345)",
    ])
    checks.extend([
        "xfer(32'h400, 1'b1, 32'hFFFFFFFF, '1, 1'b1, 1'b1, '0);",
        "xfer(32'h400, 1'b0, '0, '0, 1'b1, 1'b0, '0);",
        "xfer(32'h400, 1'b1, 32'hABCD1234, 4'h5, 1'b0, 1'b0, '0);",
        "xfer(32'h400, 1'b0, '0, '0, 1'b1, 1'b0, 32'h00CD0034);",
        "xfer(32'h410, 1'b0, '0, '0, 1'b1, 1'b0, 32'hAB321001);",
        "xfer(32'h414, 1'b0, '0, '0, 1'b1, 1'b0, 32'hFF012345);",
        "xfer(32'h410, 1'b1, '0, '1, 1'b1, 1'b1, '0);",
        "xfer(32'h598, 1'b0, '0, '0, 1'b0, 1'b1, '0);",
    ])
    for index in range(32):
        values = [randomizer.getrandbits(32) for _ in range(3)]
        checks += [
            f"counters[{index}] = 32'h{values[0]:08x};",
            f"infos[{index}] = 64'h{values[2]:08x}{values[1]:08x};",
        ]
        for address, expected in zip((0x418 + 4 * index, 0x498 + 8 * index, 0x49C + 8 * index), values):
            checks.append(f"xfer(32'h{address:x}, 1'b0, '0, '0, 1'b1, 1'b0, 32'h{expected:08x});")

    path, rtl = plan(book, out=directory)[0]
    path.write_text(rtl, encoding="utf-8")
    bench = directory / "apb_tb.sv"
    bench.write_text(
        (HERE / "apb_tb.sv.in").read_text(encoding="utf-8")
        .replace("@DECLARATIONS@", "\n  ".join(declarations))
        .replace("@PORTS@", ",\n    ".join(ports))
        .replace("@CHECKS@", "\n    ".join(checks)),
        encoding="utf-8",
    )
    return path, bench


def make_regbus_test(directory):
    book = workbook("regbus")
    sheet = book["Registers"]
    register(sheet, "configword", 0, reset="0x12345678")
    register(sheet, "pulse", 12, "W1P")
    register(sheet, "status", 24, "RC", "0xABCDEF00")
    path, rtl = plan(book, data_width=96, out=directory)[0]
    path.write_text(rtl, encoding="utf-8")
    return path, HERE / "regbus_tb.sv"


def make_zero_offset_test(directory):
    book = workbook()
    register(book["Registers"], "only", 0, reset="0xABC")
    path, rtl = plan(book, out=directory)[0]
    path.write_text(rtl, encoding="utf-8")
    bench = directory / "apb_tb.sv"
    bench.write_text(
        (HERE / "apb_tb.sv.in").read_text(encoding="utf-8")
        .replace("@DECLARATIONS@", "logic [31:0] value;")
        .replace("@PORTS@", ".block_only_value(value)")
        .replace("@CHECKS@", """
    xfer(32'h2, 1'b0, '0, '0, 1'b0, 1'b1, '0);
    xfer(32'h2, 1'b1, '1, '1, 1'b0, 1'b1, '0);
    xfer(32'h0, 1'b0, '0, '0, 1'b0, 1'b0, 32'hABC);
"""),
        encoding="utf-8",
    )
    return path, bench


SCENARIOS = (("apb_tb", make_apb_test), ("regbus_tb", make_regbus_test), ("apb_tb", make_zero_offset_test))


class RtlTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("pyslang"), "pyslang is not installed")
    def test_systemverilog_elaboration(self):
        from pyslang import DiagnosticEngine, ast, syntax

        for top, scenario in SCENARIOS:
            with self.subTest(scenario=scenario.__name__), tempfile.TemporaryDirectory() as tmp:
                files = scenario(Path(tmp))
                compilation = ast.Compilation()
                for filename in files:
                    compilation.addSyntaxTree(syntax.SyntaxTree.fromFile(str(filename)))
                compilation.getRoot()
                errors = [item for item in compilation.getAllDiagnostics() if item.isError()]
                self.assertFalse(errors, DiagnosticEngine.reportAll(compilation.sourceManager, errors))
        compilation = ast.Compilation()
        for filename in EXAMPLES.glob("*.sv"):
            compilation.addSyntaxTree(syntax.SyntaxTree.fromFile(str(filename)))
        compilation.getRoot()
        errors = [item for item in compilation.getAllDiagnostics() if item.isError()]
        self.assertFalse(errors, DiagnosticEngine.reportAll(compilation.sourceManager, errors))

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog is not on PATH")
    def test_clocked_simulation(self):
        for top, scenario in SCENARIOS:
            with self.subTest(scenario=scenario.__name__), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                simulation = directory / "test.vvp"
                files = scenario(directory)
                for command in (["iverilog", "-g2012", "-s", top, "-o", simulation, *files],
                                ["vvp", "-n", simulation]):
                    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
