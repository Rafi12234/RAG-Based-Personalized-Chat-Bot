
import express from "express";
import cors from "cors";
import helmet from "helmet";

import { env } from "./config/env.js";
import { prisma } from "./config/prisma.js";

const app = express();

// Basic security
app.disable("x-powered-by");
app.use(helmet());

// Cross-origin configuration
app.use(
  cors({
    origin: env.FRONTEND_URL,
  }),
);

// Request body parsing
app.use(express.json({ limit: "1mb" }));

// General application health
app.get("/api/v1/health", (_req, res) => {
  res.status(200).json({
    success: true,
    message: "DocMind-AI API is running",
    environment: env.NODE_ENV,
  });
});

// Database connectivity health
app.get("/api/v1/health/database", async (_req, res) => {
  try {
    await prisma.$queryRaw`SELECT 1`;

    res.status(200).json({
      success: true,
      database: "connected",
    });
  } catch {
    console.error("Database health check failed");

    res.status(503).json({
      success: false,
      database: "unavailable",
    });
  }
});

// Not-found handler
app.use((_req, res) => {
  res.status(404).json({
    success: false,
    message: "Route not found",
  });
});

export default app;
