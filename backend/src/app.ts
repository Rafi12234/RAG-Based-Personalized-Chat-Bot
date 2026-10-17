
import express from "express";
import cors from "cors";
import helmet from "helmet";
import { env } from "./config/env.js";

const app = express();

app.disable("x-powered-by");

app.use(helmet());

app.use(cors({
  origin: env.FRONTEND_URL,
}));

app.use(express.json({ limit: "1mb" }));

app.get("/api/v1/health", (_req, res) => {
  res.status(200).json({
    success: true,
    message: "DocMind-AI API is running",
    environment: env.NODE_ENV,
  });
});

app.use((_req, res) => {
  res.status(404).json({
    success: false,
    message: "Route not found",
  });
});

export default app;
