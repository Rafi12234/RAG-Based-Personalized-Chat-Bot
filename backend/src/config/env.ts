
import "dotenv/config";
import { z } from "zod";

const envSchema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"])
    .default("development"),
  PORT: z.coerce.number().int().min(1).max(65535)
    .default(4000),
  FRONTEND_URL: z.url()
    .default("http://localhost:5173"),
});

export const env = envSchema.parse(process.env);
