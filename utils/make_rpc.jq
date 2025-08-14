#
# make_rpc.jq
#
# For each top‐level value (your test object), grab
# engineNewPayloads[0].params (an array of 4 items)
# and wrap it in the JSON-RPC envelope.
#
.[] 
| .engineNewPayloads[0].params as $params
| {
    jsonrpc: "2.0",
    id: 1,
    method: "engine_newPayloadV4",
    params: $params
  }
