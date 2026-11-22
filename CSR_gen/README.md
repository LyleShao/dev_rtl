# Excel 生成多 block CSR

标准模板见 `outputs/csr/csrgen_template.xlsx`，包含两个 REG block、一个 MEMORY block、30 个普通寄存器和一个 32 路中断宏。两张寄存器表合计覆盖全部 26 种 Access。

从项目根目录运行以下命令。Excel 模板和两份示例 RTL 位于 `outputs/csr`。

```powershell
python -m pip install -r requirements.txt
python csr_gen.py outputs/csr/csrgen_template.xlsx --out build
```

每个 REG block 由 `Description.Interface` 选择一个接口，生成 `<sys_name>_<block>_csr_reg.sv`，例如 `demo_system_csr_reg.sv`。文件名与模块名一致。默认地址宽度和数据宽度均为 32 位。使用 `--addr-width` 和 `--data-width` 配置宽度。regbus 数据宽度可取 32 的正整数倍，含 APB4 REG block 的工作簿使用 32 位数据宽度。

两种接口共用 Jinja2 模板 `templates/csr.sv.j2`。接口信号、地址译码和字段访问行为均在模板中定义。生成器从脚本所在目录定位模板。

## 范例速查

| Block | 系统地址范围 | 接口和工作表 | 生成文件 |
| --- | --- | --- | --- |
| SYSTEM | `[0x0000, 0x1000)` | regbus · Register | `demo_system_csr_reg.sv` |
| SRAM | `[0x1000, 0x2000)` | regbus · MEMORY | 无 CSR 输出 |
| IRQ | `[0x2000, 0x2800)` | APB · Interrupts | `demo_irq_csr_reg.sv` |

下表使用相对 block 起点的字节偏移。

| 范例 | Offset | 可检查的行为 |
| --- | --- | --- |
| SYSTEM.CTRL | `0x0000` | 合并字段行、RO 状态和 RW 配置同字、两个标量端口 |
| SYSTEM.COMMAND | `0x0004` | 整字命令寄存器。混用 WO、W1S 和 W1T，读回零，写入时各字段执行各自操作 |
| SYSTEM.CLEAR_ON_READ | `0x000C` | RC 复位后先读回 `0xF3`，再次读取为零 |
| SYSTEM.KICK / BOOT_LOCK / KEY | `0x0028` / `0x002C` / `0x0030` | 分别演示 W1P 单拍脉冲、W1 只写一次和 WO1 只写一次且禁止读回 |
| IRQ.SECURE_CFG | `0x0008` | 安全读写，复位值 `0x0000_3C12`。安全写若仅使能字节 0 并写入 `0xA5`，读回 `0x0000_3CA5` |
| IRQ.READ_CLEAR_RW 至 WRITE0_CLEAR_READ_SET | `0x000C`–`0x0028` | 组合读写副作用，各类型放在独立寄存器中 |
| IRQ.LOCKED_BYTES | `0x002C` | 16 位 W1 跨两个 APB 字节。首次部分写入也会锁定整个字段 |
| IRQ.EVENT / AFTER_EVENT | `0x0400` / `0x0600` | 32 路中断宏占用 `[0x0400, 0x0600)`，普通寄存器紧随保留区 |
| SYSTEM.LAST / IRQ.LAST_STATUS | `0x0FFC` / `0x07FC` | 两个 block 各自的最后一个完整 32 位字 |

## Excel 格式

### Description

第 1 行为 `sys name`，系统名填在 B1，使用以 ASCII 字母开头的字母、数字和下划线。生成文件名时系统名和 block 名统一转小写。第 2 行为 `Version`，版本填在 B2。当前版本为 `r1p0`，运行时必须与 `csr_gen.py` 的 `VERSION` 完全一致。后续由开发者手动更新两处。可用 `python csr_gen.py --version` 查询脚本版本。

第 3 行为 `Block, Type, START, END, Sheet, Interface`，从第 4 行起每行定义一个地址块。

地址、偏移和复位值（`START`、`END`、`Offset`、`Reset`）使用十六进制文本。支持 `0xFC`、`0X00_00FC` 和 SystemVerilog 写法 `32'h0000_00FC`，前导零可省略，十六进制字母可大写或小写。使用带位宽的写法时，数值必须能放入声明位宽，并满足地址范围或字段宽度。`Bits` 使用十进制位编号。

| 列 | 填写规则 |
| --- | --- |
| Block | 唯一的块名，用于生成 RTL 模块名 |
| Type | 从下拉菜单选择 `REG` 或 `MEMORY`。MEMORY 参与地址检查，跳过 RTL 生成 |
| START / END | 系统字节地址，采用左闭右开区间 `[START, END)`。起点和长度均须按 1024 字节对齐，区间不可重叠或超出地址位宽 |
| Sheet | REG 必须填写对应工作表的名称，例如 `Register`、`Interrupts`，匹配时忽略大小写和首尾空格。每个 REG 使用独立工作表，MEMORY 可留空 |
| Interface | 从下拉菜单选择 `APB` 或 `regbus`。MEMORY 也需指定接口，以便应用地址窗口规则 |

例如 `[0x0000, 0x1000)` 内最后一个完整的 4 字节访问从 `0x0FFC` 开始。选择 `regbus` 的块还必须能映射为 master 的单个窗口，因此长度须为 2 的幂，起点须按该长度对齐。

### REG 工作表

首行为 `Register, Offset, Field, Bits, Access, Reset, Secure Type, Interrupt`。普通寄存器选 `Interrupt=NO`，每行定义一个字段。`Register` 在第一列，可纵向合并同一寄存器的字段行。`Offset`、`Secure Type` 和 `Interrupt` 可同步合并，也可仅在寄存器合并组的首行填写。若使用普通行，则每行重复填写这些寄存器级配置。

`Secure Type` 从下拉菜单选择 `secure` 或 `non-secure`。同一寄存器的所有字段使用同一安全类型。regbus 块的每个寄存器必须填写 `non-secure`，生成器会拒绝 `secure`。

`Bits` 使用 `[A:B]` 或单 bit 的 `[X]`，位号满足 `0 <= B <= A <= 31`。每个寄存器的行按位号从高到低排列，连续覆盖 bit 31 到 bit 0。未用位用 `reserve` 填充，可重复出现，Access 和 Reset 留空。保留位读零、写忽略。宽于 32 位的 regbus 访问中，寄存器占数据低 32 位，高位读零、写忽略。

`Offset` 是相对于 block START 的字节偏移，须按总线数据字节数对齐，完整的访问字必须落在 block 内。块名、寄存器名和字段名由 ASCII 字母开头，可包含数字和下划线，生成时统一转小写。Reset 留空时默认为 `0x0`。

### APB 中断宏

选择 `Interrupt=YES` 时，一行定义一个 32 路中断宏。填写 `Register` 和 `Offset`，其余字段配置忽略。宏只适用于 APB，起点须按 4 字节对齐，并在块内预留 `[Offset, Offset + 0x200)`。宏下面的寄存器偏移至少为 `Offset + 0x200`。例如宏位于 `0x400`，下一寄存器最早位于 `0x600`。

| 相对宏起点 | 寄存器 | Access |
| --- | --- | --- |
| `0x000` / `0x004` / `0x008` / `0x00C` | `intr_enable` / `intr_mask` / `intr_clear` / `intr_inject`，各 32 位 | RW |
| `0x010` / `0x014` | `intr_status_raw` / `intr_status_masked`，各 32 位 | RO |
| `0x018 + 4*i` | `intr_counter[i]`，32 位 | RO |
| `0x098 + 8*i` / `0x09C + 8*i` | `intr_info[i][31:0]` / `intr_info[i][63:32]` | RO |
| `0x198` 至 `0x1FF` | 保留 | — |

`i` 为中断源编号 0～31。宏内读允许安全和非安全访问，所有写要求 `PPROT[1]=0`。非安全写返回 `PSLVERR`，无寄存器副作用。四个 RW 字由 CSR 存储并复位为零，`clear` 和 `inject` 输出保存最近写入值。状态、计数和信息由中断逻辑提供，接入的硬件负责在复位时将这些 RO 输入置零。计数和信息分别通过 `[31:0][31:0]` 与 `[31:0][63:0]` 打包输入。

### 字段 Access 行为

| Access | 读行为 | 写行为 |
| --- | --- | --- |
| RO | 返回硬件输入 `<block>_<register>_<field>` | 忽略 |
| RW | 返回当前值 | 用写数据覆盖 |
| RC | 返回清除前的值并清零 | 忽略 |
| RS | 返回置位前的值并将字段置全 1 | 忽略 |
| WRC / WRS | 同 RC / RS | 用写数据覆盖 |
| WC / WS | 返回当前值 | 清零／置全 1 |
| WSRC / WCRS | 同 RC / RS | 置全 1／清零 |
| W1C / W1S / W1T | 返回当前值 | 对写入 1 的位清零／置位／翻转 |
| W0C / W0S / W0T | 返回当前值 | 对写入 0 的位清零／置位／翻转 |
| W1P | 返回零 | 对写入 1 的位输出一个时钟周期的脉冲 |
| W1SRC / W0SRC | 同 RC | 对写入 1／0 的位置位 |
| W1CRS / W0CRS | 同 RS | 对写入 1／0 的位清零 |
| WO | 读错误 | 用写数据覆盖 |
| WOC / WOS | 读错误 | 清零／置全 1 |
| W1 | 返回当前值 | 复位后仅首次有效写入更新 |
| WO1 | 读错误 | 复位后仅首次有效写入更新 |

字段端口统一命名为 `<block>_<register>_<field>`。RO 是输入，其余类型是输出。单 bit 端口声明为标量，例如 `system_ctrl_enable`，读写使用 `[0]` 这样的单 bit 选择。中断宏端口遵循相同规则，例如 `irq_event_intr_counter`。

RO 和 W1P 的 Reset 留空或填 `0x0`。CSR 的存储状态由复位和总线访问驱动。RO 字段直接读取对应输入端口。

生成器会拒绝字段端口与其他生成信号或协议端口重名。

总线按寄存器字访问。包含 WO、WOC、WOS 或 WO1 的寄存器整字不可读，读请求无字段副作用。APB 返回 `PSLVERR` 和零数据。现有 regbus ACK 无错误位，因此返回零数据 ACK。

APB 的 `PSTRB` 限定所有写操作的字节范围。W1 和 WO1 按字段锁定，只有至少一个有效写字节覆盖字段时才消耗首次写入机会。

### 混合 Access 使用提示

Excel 的 `Description` 在地址块列表下方包含英文 `Access Usage Guidelines`。这些提示用于设计评审。新地址块请插入提示标题上方，放在提示区下方的地址块会报错。

| 不建议的操作 | 原因及处理方式 |
| --- | --- |
| 把 `WO/WOC/WOS/WO1` 与需要读回或需要读副作用的字段放在同一寄存器 | 当前实现拒绝整字读。需要独立读取时拆分地址。 |
| 为读取其他字段而读含 `RC/RS/WRC/WRS/WSRC/WCRS/W1SRC/W0SRC/W1CRS/W0CRS` 的寄存器 | 读操作会对同字的读清或读置位字段产生副作用。需要独立读取时拆分地址。 |
| 将 `WC/WS/WSRC/WCRS/WOC/WOS` 或 `W1/WO1` 与需要独立写入的字段共用写覆盖范围 | 写入可能顺带清零、置位或消耗首次写入机会。regbus 写覆盖整字，APB 的 `PSTRB` 只能隔离不同字节。 |
| 对跨字节的 `W1/WO1` 分多笔 APB 写入 | 首次命中任一字节就锁定整个字段。需要在同一笔写中使能字段覆盖的全部目标字节。 |
| 对混合类型寄存器直接读改写 | 写回读快照可能清除、置位或翻转旁路字段。为 `W1C/W1S/W1T/W1P/W1SRC/W1CRS` 的旁路字段写全零，为 `W0C/W0S/W0T/W0SRC/W0CRS` 写全一。普通可写字段仍需正确保留原值。 |
| 多个写者同时读改写同一寄存器 | 旧快照可能覆盖其他写者的更新。软件写者之间应串行化整个读改写过程。硬件可在两笔总线事务之间更新字段时，应定义字段所有权或使用独立更新路径。 |
| 用上升沿计数判断背靠背的 `W1P` 写入 | 两次连续握手可能使脉冲连续两拍为高。需要逐拍采样，或由上游保证两次写入之间有空拍。 |

## regbus 集成

将模块的 `s_regbus_*` 端口接到 `regbus_generic_master` 对应 slave 索引。`SLAVE_ADDR_BASE[i]` 设为 START，`SLAVE_ADDR_MASK[i]` 设为指定位宽内的 `~(END - START - 1)`。master 负责基址译码并传入相对字节地址。模块使用低有效异步复位 `rst_n`，请求握手时采样读值并执行副作用。单项 ACK 缓冲在反压时保持 valid 和数据稳定，支持旧 ACK 被接收的同一拍接收下一笔请求。

写请求 ACK 数据为零。保留位读零、写忽略。低位译码未命中寄存器时返回零 ACK。master 处理窗口外未命中的请求及其 error 脉冲。

regbus 和 APB 的地址端口均接收上游路由后的相对字节地址，端口宽度为 `--addr-width`。每个 block 的译码覆盖最大寄存器 Offset 和中断宏的完整保留区间，并保留字内地址位。32 位接口即使只有 offset `0x0`，也比较 `[1:0]`，避免把 `0x2` 当作 `0x0`。最大译码偏移为 `0x104` 时比较 `[8:0]`。高位裁剪后，低位相同的偏移会命中同一个寄存器，上游仍须过滤无效偏移。

## APB4 集成

`paddr` 使用相对字节地址，字段按寄存器 `Offset` 译码。模块使用 `pclk` 和低有效异步复位 `presetn`，正常运行时零等待，副作用仅在 `psel && penable && pready` 握手时发生。`pstrb` 控制写入的字节，包括 W0x 和 W1x 字段。低位译码未命中寄存器时，读数据为零，握手时返回 `pslverr`。

APB4 的 `pprot[1]` 为 0 表示安全访问，为 1 表示非安全访问。`secure` 寄存器仅允许安全读写。非安全访问返回 `pslverr`，读数据为零，也不会触发写入或 RC 清零。`non-secure` 寄存器允许两种访问。参见 [Arm AMBA APB 规范](https://documentation-service.arm.com/static/63fe2c1356ea36189d4e79f3?token=)。

## 回归测试

```powershell
python -m pip install -r requirements.txt
python -B -m unittest discover -s tests -v
```

Python 回归覆盖合法与损坏工作簿、版本不匹配、十六进制写法、合并字段、端口命名冲突、地址边界、APB/regbus 多 block，以及含 16 个中断宏和 1,024 个普通寄存器的地址表。回归也会比较模板生成结果和仓库里的示例 RTL。

安装 `pyslang` 后，同一命令会对示例 RTL 和三个测试平台执行 SystemVerilog elaboration。将 `iverilog` 和 `vvp` 加入 PATH 后，还会运行时序仿真，包括 APB 的全部 26 种 Access、16 种字节使能、权限检查、96 个中断数据字映射，以及 96 位 regbus 的 2,000 周期固定种子随机反压。缺少对应工具时，相关用例会标为跳过。

