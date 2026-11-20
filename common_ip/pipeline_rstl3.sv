module pipeline_rstl3 #(
    parameter DATA_WIDTH = 32,
    parameter RAND_STALL = 0
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic [2:0]              stall_cfg,

    input  logic                    s_valid,
    output logic                    s_ready,
    input  logic [DATA_WIDTH-1:0]   s_data,

    output logic                    m_valid,
    input  logic                    m_ready,
    output logic [DATA_WIDTH-1:0]   m_data
);

    logic                   random_stall;
    logic                   full_q;
    logic [DATA_WIDTH-1:0]  data_q;

    generate
        if (RAND_STALL) begin : g_random_stall
            random_stall_gen u_random_stall_gen (
                .clk          (clk),
                .rst_n        (rst_n),
                .stall_cfg    (stall_cfg),
                .random_stall (random_stall)
            );
        end else begin : g_no_random_stall
            assign random_stall = 1'b0;
        end
    endgenerate

    assign s_ready = !random_stall && !full_q;
    assign m_valid = !random_stall && (full_q || s_valid);
    assign m_data  = full_q ? data_q : s_data;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            full_q <= 1'b0;
        else if (!random_stall) begin
            if (full_q)
                full_q <= !m_ready;
            else
                full_q <= s_valid && !m_ready;
        end
    end

    always_ff @(posedge clk) begin
        if (rst_n && s_valid && s_ready && !m_ready)
            data_q <= s_data;
    end

endmodule