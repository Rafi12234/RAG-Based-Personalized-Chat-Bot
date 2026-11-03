import type { ErrorRequestHandler, RequestHandler } from "express";
import multer from "multer";

export class HttpError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    /** Optional internal reason code; never expose outside explicitly opted-in development diagnostics. */
    public readonly diagnosticCode?: string,
  ) {
    super(message);
    this.name = "HttpError";
  }
}

export const notFound: RequestHandler = (_req, res) => {
  res.status(404).json({ success: false, message: "Route not found" });
};

export const errorHandler: ErrorRequestHandler = (error: unknown, _req, res, _next) => {
  if (error instanceof HttpError) {
    // Local development only: a short allowlisted reason helps distinguish an
    // invalid model attribution from a transport outage. Never expose source
    // text, prompts, model output, SQL messages or arbitrary exception details.
    if (process.env.NODE_ENV === "development" &&
        process.env.DOCMIND_ANSWER_DIAGNOSTICS === "1" &&
        error.status === 503 && error.message === "Answer service unavailable" &&
        typeof error.diagnosticCode === "string" &&
        /^[a-z0-9_]{1,64}$/u.test(error.diagnosticCode)) {
      res.setHeader("X-DocMind-Diagnostic-Code", error.diagnosticCode);
      res.setHeader("Access-Control-Expose-Headers", "X-DocMind-Diagnostic-Code");
    }
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
  if (error && typeof error === "object" && "code" in error && error.code === "P2003") {
    res.status(409).json({ success: false, message: "Resource has dependent records" });
    return;
  }
  if (error && typeof error === "object" && "code" in error && error.code === "P2002") {
    res.status(409).json({ success: false, message: "A record with those details already exists" });
    return;
  }
  // A transient adapter connection failure is an unavailable dependency, not
  // an unexpected application bug. Never expose driver messages to callers.
  // Only match this documented Prisma driver-adapter error shape.
  if (error && typeof error === "object" && "code" in error &&
      error.code === "P2039" && "meta" in error &&
      error.meta && typeof error.meta === "object" &&
      "driverAdapterError" in error.meta) {
    const driver = error.meta.driverAdapterError;
    if (driver && typeof driver === "object" && "message" in driver &&
        typeof driver.message === "string" &&
        driver.message.includes("pool failed to retrieve a connection from pool")) {
      console.error("DocMind database diagnostic: connection_pool_unavailable");
      res.status(503).json({ success: false, message: "Database temporarily unavailable" });
      return;
    }
  }
  console.error("Unhandled API error:", error);
  res.status(500).json({ success: false, message: "Unexpected server error" });
};
