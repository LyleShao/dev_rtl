module regbus_tb;
  logic clk = 0;
  logic rst_n = 0;
  logic req_valid = 0, req_ready, req_write, ack_valid, ack_ready = 0;
  logic [31:0] req_addr;
  logic [95:0] wdata, rdata;
  logic [31:0] config_value, pulse_value, status_value;

  test_block_csr_reg dut (
    .clk, .rst_n,
    .s_regbus_req_valid(req_valid), .s_regbus_req_ready(req_ready),
    .s_regbus_req_write(req_write), .s_regbus_req_addr(req_addr), .s_regbus_req_wdata(wdata),
    .s_regbus_ack_valid(ack_valid), .s_regbus_ack_ready(ack_ready), .s_regbus_ack_rdata(rdata),
    .block_configword_value(config_value), .block_pulse_value(pulse_value),
    .block_status_value(status_value)
  );

  always #5 clk = ~clk;

  function automatic logic [31:0] next_random(input logic [31:0] value);
    value ^= value << 13;
    value ^= value >> 17;
    return value ^ (value << 5);
  endfunction

  logic [31:0] random_bits = 32'h23AB10F1;
  logic [31:0] expected_config = 32'h12345678;
  logic [31:0] expected_status = 32'hABCDEF00;
  logic [31:0] expected_pulse = 0;
  logic [95:0] expected_data = 0;
  logic expected_valid = 0, held_request = 0, accepted;
  integer accepted_count = 0, stall_count = 0, replaced_count = 0;

  initial begin
    @(negedge clk);
    rst_n = 1;
    for (integer cycle = 0; cycle < 2000; cycle++) begin
      random_bits = next_random(random_bits);
      ack_ready = random_bits[0];
      if (!held_request) begin
        req_valid = |random_bits[2:1];
        req_write = random_bits[9];
        case (random_bits[11:10])
          0: req_addr = 0;
          1: req_addr = 12;
          2: req_addr = 24;
          3: req_addr = 2;
        endcase
        wdata = {32'hFEDCBA98, 32'h89ABCDEF, random_bits};
      end
      #1;
      if (req_ready !== (!expected_valid || ack_ready)) $fatal(1, "ready before cycle %0d", cycle);
      if (ack_valid !== expected_valid || (expected_valid && rdata !== expected_data))
        $fatal(1, "ack before cycle %0d", cycle);
      accepted = req_valid && req_ready;
      held_request = req_valid && !req_ready;
      if (held_request) stall_count++;
      if (accepted && expected_valid && ack_ready) replaced_count++;
      if (ack_ready) expected_valid = 0;
      expected_pulse = 0;
      if (accepted) begin
        accepted_count++;
        expected_valid = 1;
        expected_data = 0;
        case (req_addr)
          0: if (req_write) expected_config = wdata[31:0];
             else expected_data[31:0] = expected_config;
          12: if (req_write) expected_pulse = wdata[31:0];
          24: if (!req_write) begin
            expected_data[31:0] = expected_status;
            expected_status = 0;
          end
          default: ;
        endcase
      end
      @(posedge clk);
      #1;
      if (ack_valid !== expected_valid || (expected_valid && rdata !== expected_data))
        $fatal(1, "ack after cycle %0d", cycle);
      if (config_value !== expected_config || pulse_value !== expected_pulse || status_value !== expected_status)
        $fatal(1, "register state after cycle %0d", cycle);
      @(negedge clk);
    end
    if (accepted_count < 400 || stall_count < 300 || replaced_count < 200)
      $fatal(1, "insufficient handshake coverage");
    rst_n = 0;
    #1;
    if (ack_valid !== 0 || req_ready !== 0 || config_value !== 32'h12345678 ||
        status_value !== 32'hABCDEF00 || pulse_value !== 0)
      $fatal(1, "reset state");
    $finish;
  end
endmodule
