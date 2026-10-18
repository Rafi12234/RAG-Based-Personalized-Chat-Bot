import type { ErrorRequestHandler, RequestHandler } from "express";
import multer from "multer";

export class HttpError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "HttpError";
  }
}

export const notFound: RequestHandler = (_req, res) => {
  res.status(404).json({ success: false, message: "Route not found" });
};

export const errorHandler: ErrorRequestHandler = (error: unknown, _req, res, _next) => {
  if (error instanceof HttpError) {
    res.status(error.status).json({ success: false, message: error.message });
    return;
  }
  if (error instanceof multer.MulterError) {
    res.status(error.code === "LIMIT_FILE_SIZE" ? 413 : 400).json({
      success: false,
      message: error.code === "LIMIT_FILE_SIZE" ? "File exceeds the 250 MB upload limit" : "Invalid file upload",
    });
    return;
  }
  if (error && typeof error === "object" && "code" in error && error.code === "P2002") {
    res.status(409).json({ success: false, message: "A record with those details already exists" });
    return;
  }
  console.error("Unhandled API error:", error);
  res.status(500).json({ success: false, message: "Unexpected server error" });
};
