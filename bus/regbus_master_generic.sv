module regbus_generic_master #(

    parameter int NUM_SLAVES = 4,
    parameter int DATA_WIDTH = 32, // Positive multiple of 32.
    parameter int ADDR_WIDTH = 32,
    // Mask ones select the address prefix. Use contiguous low zeros (at least
    // ten) for a naturally aligned, power-of-two window of at least 1 KiB.
    // Set base bits outside the mask to zero for an enabled window.
    // The default all-ones base and zero mask disable the slot.
    // Enabled windows must not overlap.
    parameter logic [NUM_SLAVES-1:0][ADDR_WIDTH-1:0] SLAVE_ADDR_BASE = '1,
    parameter logic [NUM_SLAVES-1:0][ADDR_WIDTH-1:0] SLAVE_ADDR_MASK = '0,
    // Dispatched requests awaiting acknowledgment by the master.
    // Must be at least one.
    parameter int OUTSTANDING_DEPTH = 8

)(

    input  logic                                  clk,
    input  logic                                  rst_n,

//===============================================================
//                         REGBUS MASTER
//===============================================================

// request channel
    input  logic                                  m_regbus_req_valid,
    output logic                                  m_regbus_req_ready,
    input  logic                                  m_regbus_req_write,
    input  logic [ADDR_WIDTH-1:0]                 m_regbus_req_addr,
    input  logic [DATA_WIDTH-1:0]                 m_regbus_req_wdata,

// acknowledge channel
    output logic                                  m_regbus_ack_valid,
    input  logic                                  m_regbus_ack_ready,
    output logic [DATA_WIDTH-1:0]                 m_regbus_ack_rdata,

//===============================================================
//                         REGBUS SLAVES
//===============================================================

// request channel
    output logic [NUM_SLAVES-1:0]                 s_regbus_req_valid,
    input  logic [NUM_SLAVES-1:0]                 s_regbus_req_ready,
    output logic [NUM_SLAVES-1:0]                 s_regbus_req_write,
    output logic [NUM_SLAVES-1:0][ADDR_WIDTH-1:0] s_regbus_req_addr,
    output logic [NUM_SLAVES-1:0][DATA_WIDTH-1:0] s_regbus_req_wdata,

// acknowledge channel
    input  logic [NUM_SLAVES-1:0]                 s_regbus_ack_valid,
    output logic [NUM_SLAVES-1:0]                 s_regbus_ack_ready,
    input  logic [NUM_SLAVES-1:0][DATA_WIDTH-1:0] s_regbus_ack_rdata,

    // Pulses after an unmapped request is discarded. It generates no ACK.
    output logic                                  m_regbus_req_error
);

    localparam int SELECT_WIDTH = NUM_SLAVES > 1 ? $clog2(NUM_SLAVES) : 1;

    typedef struct packed {
        logic                  write;
        logic [ADDR_WIDTH-1:0] addr;
        logic [DATA_WIDTH-1:0] wdata;
    } request_t;

    localparam int COUNT_WIDTH = $clog2(OUTSTANDING_DEPTH + 1);

    request_t master_request;
    logic request_valid, request_ready, request_hit, dispatch;
    logic [SELECT_WIDTH-1:0] request_slave, active_slave_q;
    logic [COUNT_WIDTH-1:0] outstanding_q;
    logic can_dispatch, response_valid, response_ready, retire;
    logic [DATA_WIDTH-1:0] response_data;

    regbus_generic_master_fifo #(.WIDTH($bits(request_t)), .DEPTH(2)) u_request (
        .clk, .rst_n,
        .in_valid(m_regbus_req_valid), .in_ready(m_regbus_req_ready),
        .in_data({m_regbus_req_write, m_regbus_req_addr, m_regbus_req_wdata}),
        .out_valid(request_valid), .out_ready(request_ready), .out_data(master_request)
    );

    always_comb begin
        request_hit = 1'b0;
        request_slave = '0;
        for (int i = NUM_SLAVES - 1; i >= 0; i--) begin
            if ((master_request.addr & SLAVE_ADDR_MASK[i]) == SLAVE_ADDR_BASE[i]) begin
                request_hit = 1'b1;
                request_slave = SELECT_WIDTH'(i);
            end
        end
    end

    // Hold the target until the master consumes its last ACK. A request for
    // another target can wait in the shared request FIFO during that time.
    assign can_dispatch = request_hit &&
                          outstanding_q < COUNT_WIDTH'(OUTSTANDING_DEPTH) &&
                          (outstanding_q == '0 || request_slave == active_slave_q);

    assign request_ready = !request_hit ||
                           (can_dispatch && s_regbus_req_ready[request_slave]);

    for (genvar i = 0; i < NUM_SLAVES; i++) begin : g_slave
        assign s_regbus_req_valid[i] = request_valid && can_dispatch &&
                                       request_slave == SELECT_WIDTH'(i);
        assign s_regbus_req_write[i] = master_request.write;
        assign s_regbus_req_addr[i] = master_request.addr & ~SLAVE_ADDR_MASK[i];
        assign s_regbus_req_wdata[i] = master_request.wdata;
        assign s_regbus_ack_ready[i] = response_ready && outstanding_q != '0 &&
                                       active_slave_q == SELECT_WIDTH'(i);
    end

    always_comb begin
        response_valid = 1'b0;
        response_data = '0;
        if (outstanding_q != '0) begin
            response_valid = s_regbus_ack_valid[active_slave_q];
            response_data = s_regbus_ack_rdata[active_slave_q];
        end
    end

    regbus_generic_master_fifo #(.WIDTH(DATA_WIDTH), .DEPTH(2)) u_response (
        .clk, .rst_n,
        .in_valid(response_valid), .in_ready(response_ready), .in_data(response_data),
        .out_valid(m_regbus_ack_valid), .out_ready(m_regbus_ack_ready),
        .out_data(m_regbus_ack_rdata)
    );

    assign dispatch = request_valid && request_ready && request_hit;
    assign retire = m_regbus_ack_valid && m_regbus_ack_ready;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            active_slave_q <= '0;
            outstanding_q <= '0;
            m_regbus_req_error <= 1'b0;
        end else begin
            m_regbus_req_error <= request_valid && request_ready && !request_hit;
            if (dispatch)
                active_slave_q <= request_slave;
            case ({dispatch, retire})
                2'b10: outstanding_q <= outstanding_q + 1'b1;
                2'b01: outstanding_q <= outstanding_q - 1'b1;
                default: ;
            endcase
        end
    end
endmodule

// Non-bypassing FIFO. Interface outputs depend only on local stored state.
module regbus_generic_master_fifo #(
    parameter int WIDTH = 32,
    parameter int DEPTH = 2
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic             in_valid,
    output logic             in_ready,
    input  logic [WIDTH-1:0] in_data,
    output logic             out_valid,
    input  logic             out_ready,
    output logic [WIDTH-1:0] out_data
);

    localparam int PTR_WIDTH = DEPTH > 1 ? $clog2(DEPTH) : 1;
    localparam int COUNT_WIDTH = $clog2(DEPTH + 1);

    logic [WIDTH-1:0] data_q [DEPTH];
    logic [PTR_WIDTH-1:0] rd_ptr_q, wr_ptr_q;
    logic [COUNT_WIDTH-1:0] count_q;
    logic push, pop;

    assign in_ready = rst_n && count_q != COUNT_WIDTH'(DEPTH);
    assign out_valid = rst_n && count_q != '0;
    assign out_data = data_q[rd_ptr_q];
    assign push = in_valid && in_ready;
    assign pop  = out_valid && out_ready;

    always_ff @(posedge clk) begin
        if (push)
            data_q[wr_ptr_q] <= in_data;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_ptr_q <= '0;
            wr_ptr_q <= '0;
            count_q  <= '0;
        end else begin
            if (push)
                wr_ptr_q <= wr_ptr_q == PTR_WIDTH'(DEPTH - 1) ? '0 : wr_ptr_q + 1'b1;
            if (pop)
                rd_ptr_q <= rd_ptr_q == PTR_WIDTH'(DEPTH - 1) ? '0 : rd_ptr_q + 1'b1;
            case ({push, pop})
                2'b10: count_q <= count_q + 1'b1;
                2'b01: count_q <= count_q - 1'b1;
                default: ;
            endcase
        end
    end

endmodule

