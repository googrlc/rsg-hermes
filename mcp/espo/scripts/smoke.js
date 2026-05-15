const baseUrl = process.env.ESPO_MCP_SMOKE_URL || "http://127.0.0.1:3000";
const token = process.env.ESPO_MCP_BEARER_TOKEN || "";

const headers = {
  "content-type": "application/json",
  accept: "application/json, text/event-stream"
};
if (token) headers.authorization = `Bearer ${token}`;

const initialize = {
  jsonrpc: "2.0",
  id: 1,
  method: "initialize",
  params: {
    protocolVersion: "2025-03-26",
    capabilities: {},
    clientInfo: { name: "rsg-espo-mcp-smoke", version: "0.1.0" }
  }
};

const initRes = await fetch(`${baseUrl}/mcp`, {
  method: "POST",
  headers,
  body: JSON.stringify(initialize)
});
if (!initRes.ok) {
  throw new Error(`initialize failed: ${initRes.status} ${await initRes.text()}`);
}
const sessionId = initRes.headers.get("mcp-session-id");
const initText = await initRes.text();
console.log(`initialize ${initRes.status} session=${sessionId || "none"}`);
console.log(initText.slice(0, 300));

const listRes = await fetch(`${baseUrl}/mcp`, {
  method: "POST",
  headers: { ...headers, "mcp-session-id": sessionId },
  body: JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} })
});
if (!listRes.ok) {
  throw new Error(`tools/list failed: ${listRes.status} ${await listRes.text()}`);
}
console.log(`tools/list ${listRes.status}`);
console.log((await listRes.text()).slice(0, 1000));
