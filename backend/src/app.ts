import express from "express";
import cors from "cors";
import helmet from "helmet";
import { env } from "./config/env.js";
import { prisma } from "./config/prisma.js";
import { authRouter } from "./routes/auth.js";
import { organizationsRouter } from "./routes/organizations.js";
import { knowledgeBasesRouter } from "./routes/knowledgeBases.js";
import { documentsRouter, knowledgeBaseDocumentsRouter } from "./routes/documents.js";
import { retrievalRouter } from "./routes/retrieval.js";
import { notFound, errorHandler, HttpError } from "./lib/http.js";

const app = express();
app.disable("x-powered-by");
app.use(helmet());
app.use(cors({ origin: env.FRONTEND_URL, credentials: true }));
app.use(express.json({ limit: "1mb" }));

// All browser-originated mutations require a non-simple header, with the
// configured frontend Origin enforced. This is a baseline cookie-CSRF defense.
app.use((req, _res, next) => {
  if (["POST", "PUT", "PATCH", "DELETE"].includes(req.method)) {
    const origin = req.get("origin");
    if (origin && origin !== env.FRONTEND_URL) return next(new HttpError(403, "Origin not allowed"));
    if (req.get("x-docmind-request") !== "1") return next(new HttpError(403, "Missing request security header"));
  }
  next();
});

app.get("/api/v1/health", (_req, res) => {
  res.json({ success: true, message: "DocMind-AI API is running", environment: env.NODE_ENV });
});
app.get("/api/v1/health/database", async (_req, res) => {
  try {
    await prisma.$queryRaw`SELECT 1`;
    res.json({ success: true, database: "connected" });
  } catch {
    res.status(503).json({ success: false, database: "unavailable" });
  }
});

app.use("/api/v1/auth", authRouter);
app.use("/api/v1/organizations", organizationsRouter);
app.use("/api/v1/knowledge-bases", knowledgeBasesRouter);
app.use("/api/v1/knowledge-bases", knowledgeBaseDocumentsRouter);
app.use("/api/v1/knowledge-bases", retrievalRouter);
app.use("/api/v1/documents", documentsRouter);
app.use(notFound);
app.use(errorHandler);
export default app;
