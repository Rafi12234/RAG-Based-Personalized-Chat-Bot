
import "dotenv/config";
import { z } from "zod";

const envSchema = z.object({
  NODE_ENV: z
    .enum(["development", "test", "production"])
    .default("development"),

  PORT: z.coerce
    .number()
    .int()
    .min(1)
    .max(65535)
    .default(4000),

  FRONTEND_URL: z.url().default("http://localhost:5173"),

  DATABASE_HOST: z.string().min(1),

  DATABASE_PORT: z.coerce
    .number()
    .int()
    .min(1)
    .max(65535)
    .default(3307),

  DATABASE_USER: z.string().min(1),

  DATABASE_PASSWORD: z.string().min(1),

  DATABASE_NAME: z.string().min(1),
});

const result = envSchema.safeParse(process.env);

if (!result.success) {
  console.error(
    "Invalid backend environment configuration:",
    result.error.flatten().fieldErrors,
  );
  throw new Error("Environment configuration is invalid");
}

export const env = result.data;
