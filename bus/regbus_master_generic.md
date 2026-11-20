# Regbus Generic Master

`regbus_generic_master` 将一路 master regbus 路由到多个 slave。请求和应答各占一拍传输，使用独立的 `valid/ready` 握手。每个时钟上升沿，只有 `valid` 和 `ready` 同时为高时才完成传输。发送方在等待握手期间应保持 `valid` 和数据稳定。

## 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `NUM_SLAVES` | 4 | Slave 数量，至少为 1。Slave 侧端口通过索引 `i` 与地址配置对应。 |
| `DATA_WIDTH` | 32 | 读写数据宽度，取 32 的正整数倍。 |
| `ADDR_WIDTH` | 32 | 字节地址宽度。 |
| `SLAVE_ADDR_BASE[i]` | 全 1 | Slave 地址窗口的起始地址。 |
| `SLAVE_ADDR_MASK[i]` | 全 0 | Slave 地址窗口的前缀掩码。默认的 BASE 和 MASK 组合禁用该窗口。 |
| `OUTSTANDING_DEPTH` | 8 | 已发给 slave、尚未被 master 握手接收 ACK 的最大请求数，至少为 1。 |

## 地址配置

窗口长度 `SIZE_BYTES` 必须为 2 的幂且至少为 1024 字节，`BASE` 必须按窗口长度对齐。启用的窗口之间不得重叠。这些条件由集成方保证。

```systemverilog
MASK          = ~(ADDR_WIDTH'(SIZE_BYTES) - ADDR_WIDTH'(1));
hit           = (addr & MASK) == BASE;
relative_addr = addr & ~MASK;
```

例如，32 位地址下，`BASE = 32'h8000_0400`、`SIZE_BYTES = 1024` 时，`MASK = 32'hFFFF_FC00`，窗口覆盖 `0x8000_0400` 至 `0x8000_07FF`。访问 `0x8000_0403` 时，`s_regbus_req_addr[i]` 为 `0x0000_0003`。相对地址保留低位字节偏移。

## 请求和应答

- `m_regbus_req_write` 为 1 表示写，为 0 表示读。对应 slave 收到请求类型、相对地址和写数据。
- Slave 每接收一笔映射请求，须按接收顺序返回一次 ACK。读请求通过 ACK 返回 `rdata`，写请求通过 ACK 确认成功。
- 所有 slave 共用一个两项请求 FIFO 和一个两项 ACK FIFO。两条通道都通过 FIFO 状态实现反压，避免 master 和 slave 之间的组合直通。
- 同一 slave 可有多笔请求在途。在 FIFO 有余量、outstanding 未达上限且两端持续握手时，可每拍发出一笔请求并返回一笔 ACK。
- 模块在 master 握手接收最后一笔在途请求的 ACK 后解除目标锁定。发往其他 slave 的请求可以先进入请求 FIFO，等锁定解除后再发出。

## 未映射地址

未命中的请求到达请求 FIFO 队首时直接出队并丢弃。该请求无 slave 侧传输、无 outstanding 记录、无 ACK。模块在丢弃后的一个周期将 `m_regbus_req_error` 拉高。若连续丢弃多笔请求，每个高电平周期代表一笔错误。

错误信号没有 `ready`，也不经过 ACK FIFO。上游应逐拍采样，并将错误请求从预期 ACK 数中扣除。已有有效请求的 ACK 可能与错误信号同拍，也可能在错误信号之后到达。
