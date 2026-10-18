import { z } from "zod";
import { HttpError } from "./http.js";

export function parseInput<T extends z.ZodType>(schema: T, input: unknown): z.infer<T> {
  const result = schema.safeParse(input);
  if (!result.success) throw new HttpError(400, "Invalid request data");
  return result.data;
}

export const idSchema = z.string().cuid();
