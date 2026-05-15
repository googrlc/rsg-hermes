import express from "express";
import { randomUUID } from "node:crypto";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";

const ESPO_URL = normalizeBaseUrl(process.env.ESPO_URL);
const ESPO_API_KEY = process.env.ESPO_API_KEY || "";
const MCP_TOKEN = process.env.ESPO_MCP_BEARER_TOKEN || "";
const PORT = Number(process.env.ESPO_MCP_PORT || 3000);
const MAX_LIST_SIZE = Number(process.env.ESPO_MCP_MAX_LIST_SIZE || 200);

if (!ESPO_URL || !ESPO_API_KEY) {
  throw new Error("ESPO_URL and ESPO_API_KEY are required.");
}

if ((process.env.HERMES_VERIFY_TLS || "").toLowerCase() === "false") {
  process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
}

const app = express();
app.use(express.json({ limit: "1mb" }));

function normalizeBaseUrl(raw) {
  const value = String(raw || "").replace(/\/+$/, "");
  if (!value) return "";
  return value.endsWith("/api/v1") ? value : `${value}/api/v1`;
}

function jsonParam(value) {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function clampLimit(limit) {
  const parsed = Number(limit || 10);
  if (!Number.isFinite(parsed)) return 10;
  return Math.max(1, Math.min(parsed, MAX_LIST_SIZE));
}

function ensureAuthorized(req, res, next) {
  if (!MCP_TOKEN) return next();
  const header = req.get("authorization") || "";
  if (header === `Bearer ${MCP_TOKEN}`) return next();
  res.status(401).json({ error: "Unauthorized" });
}

async function espoRequest(path, { params = {} } = {}) {
  const url = new URL(`${ESPO_URL}/${path.replace(/^\/+/, "")}`);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, Array.isArray(value) || typeof value === "object" ? jsonParam(value) : String(value));
    }
  }
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
      "X-Api-Key": ESPO_API_KEY
    }
  });
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { raw: text.slice(0, 500) };
  }
  if (!response.ok) {
    throw new Error(`${response.status} GET ${path}: ${JSON.stringify(body).slice(0, 700)}`);
  }
  return body;
}

function result(data) {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(data, null, 2)
      }
    ]
  };
}

function listFromEspo(body) {
  return Array.isArray(body?.list) ? body.list : [];
}

function createServer() {
  const server = new McpServer({
    name: "RSG EspoCRM MCP",
    version: "0.1.0"
  });

  server.registerTool(
    "search_contacts",
    {
      title: "Search Contacts",
      description: "Search EspoCRM contacts by name or email. Read-only.",
      inputSchema: {
        query: z.string().min(2),
        limit: z.number().int().min(1).max(50).default(10)
      }
    },
    async ({ query, limit }) => {
      const body = await espoRequest("Contact", {
        params: {
          maxSize: clampLimit(limit),
          select: "id,name,emailAddress,phoneNumber,accountName,assignedUserName",
          where: [
            {
              type: "or",
              value: [
                { type: "contains", attribute: "name", value: query },
                { type: "contains", attribute: "emailAddress", value: query },
                { type: "contains", attribute: "phoneNumber", value: query }
              ]
            }
          ]
        }
      });
      return result({ entity: "Contact", count: listFromEspo(body).length, records: listFromEspo(body) });
    }
  );

  server.registerTool(
    "search_accounts",
    {
      title: "Search Accounts",
      description: "Search EspoCRM accounts/companies by name. Read-only.",
      inputSchema: {
        query: z.string().min(2),
        limit: z.number().int().min(1).max(50).default(10)
      }
    },
    async ({ query, limit }) => {
      const body = await espoRequest("Account", {
        params: {
          maxSize: clampLimit(limit),
          select: "id,name,emailAddress,phoneNumber,billingAddressCity,billingAddressState,assignedUserName",
          where: [{ type: "contains", attribute: "name", value: query }]
        }
      });
      return result({ entity: "Account", count: listFromEspo(body).length, records: listFromEspo(body) });
    }
  );

  server.registerTool(
    "get_crm_record",
    {
      title: "Get CRM Record",
      description: "Retrieve one EspoCRM record by entity and id. Read-only.",
      inputSchema: {
        entity: z.enum(["Account", "Contact", "Lead", "Opportunity", "Task", "Note"]),
        id: z.string().min(1)
      }
    },
    async ({ entity, id }) => result(await espoRequest(`${entity}/${encodeURIComponent(id)}`))
  );

  server.registerTool(
    "list_open_tasks",
    {
      title: "List Open Tasks",
      description: "List open EspoCRM tasks, optionally filtered by text. Read-only.",
      inputSchema: {
        query: z.string().optional().default(""),
        limit: z.number().int().min(1).max(50).default(10)
      }
    },
    async ({ query, limit }) => {
      const where = [
        {
          type: "notIn",
          attribute: "status",
          value: ["Completed", "Cancelled"]
        }
      ];
      if (query) {
        where.push({ type: "contains", attribute: "name", value: query });
      }
      const body = await espoRequest("Task", {
        params: {
          maxSize: clampLimit(limit),
          orderBy: "dateStart",
          order: "asc",
          select: "id,name,status,dateStart,dateEnd,assignedUserName,parentName,parentType",
          where
        }
      });
      return result({ entity: "Task", count: listFromEspo(body).length, records: listFromEspo(body) });
    }
  );

  return server;
}

const transports = new Map();

app.get("/health", (_req, res) => {
  res.json({ status: "ok", service: "rsg-espo-mcp", tools: 4 });
});

app.all("/mcp", ensureAuthorized, async (req, res) => {
  try {
    const sessionId = req.headers["mcp-session-id"];
    let transport;

    if (sessionId && transports.has(sessionId)) {
      transport = transports.get(sessionId);
    } else if (!sessionId && isInitializeRequest(req.body)) {
      transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
        onsessioninitialized: (id) => transports.set(id, transport)
      });
      transport.onclose = () => {
        if (transport.sessionId) transports.delete(transport.sessionId);
      };
      await createServer().connect(transport);
    } else {
      res.status(400).json({ jsonrpc: "2.0", error: { code: -32000, message: "Bad Request: invalid MCP session" }, id: null });
      return;
    }

    await transport.handleRequest(req, res, req.body);
  } catch (error) {
    console.error("MCP request failed", error);
    if (!res.headersSent) {
      res.status(500).json({ jsonrpc: "2.0", error: { code: -32603, message: "Internal server error" }, id: null });
    }
  }
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`RSG EspoCRM MCP listening on 0.0.0.0:${PORT}`);
});
