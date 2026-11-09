
import { PrismaMariaDb } from "@prisma/adapter-mariadb";
import { PrismaClient } from "../generated/prisma/client.js";

import { env } from "./env.js";
import { allowLocalDevMySqlKeyRetrieval } from "../lib/localMySqlAuth.js";
import { databasePoolTimeouts } from "../lib/databasePoolPolicy.js";

const adapter = new PrismaMariaDb({
  host: env.DATABASE_HOST,
  port: env.DATABASE_PORT,
  user: env.DATABASE_USER,
  password: env.DATABASE_PASSWORD,
  database: env.DATABASE_NAME,
  connectionLimit: 5,
  // Local Docker sockets occasionally exceed the 10-second connection
  // deadline under concurrent frontend/AI workloads. Keep the five-connection
  // limit; only relax bounded timeouts for the known local development port.
  ...databasePoolTimeouts(env.NODE_ENV, env.DATABASE_HOST, env.DATABASE_PORT),
  allowPublicKeyRetrieval: allowLocalDevMySqlKeyRetrieval(env.NODE_ENV, env.DATABASE_HOST, env.DATABASE_PORT),
});

export const prisma = new PrismaClient({
  adapter,
  log:
    env.NODE_ENV === "development"
      ? ["warn", "error"]
      : ["error"],
});
