
import { PrismaMariaDb } from "@prisma/adapter-mariadb";
import { PrismaClient } from "../generated/prisma/client.js";

import { env } from "./env.js";
import { allowLocalDevMySqlKeyRetrieval } from "../lib/localMySqlAuth.js";

const adapter = new PrismaMariaDb({
  host: env.DATABASE_HOST,
  port: env.DATABASE_PORT,
  user: env.DATABASE_USER,
  password: env.DATABASE_PASSWORD,
  database: env.DATABASE_NAME,
  connectionLimit: 5,
  // Keep the pool at five connections. A local Docker/MySQL socket can
  // sometimes take longer to initialize; acquisition timeout must exceed
  // connection timeout to preserve the underlying driver error category.
  connectTimeout: 10_000,
  acquireTimeout: 20_000,
  allowPublicKeyRetrieval: allowLocalDevMySqlKeyRetrieval(env.NODE_ENV, env.DATABASE_HOST, env.DATABASE_PORT),
});

export const prisma = new PrismaClient({
  adapter,
  log:
    env.NODE_ENV === "development"
      ? ["warn", "error"]
      : ["error"],
});
