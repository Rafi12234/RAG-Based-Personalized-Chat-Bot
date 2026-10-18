import app from "./app.js";
import { env } from "./config/env.js";
import { prisma } from "./config/prisma.js";

async function startServer() {
  await prisma.$connect();
  console.log("MySQL connection established");

  const server = app.listen(env.PORT, () => {
    console.log(`DocMind-AI API listening at http://localhost:${env.PORT}`);
  });
  let shuttingDown = false;

  const shutdown = (signal: string) => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`${signal}: shutting down`);
    const timeout = setTimeout(() => {
      console.error("Grace period exceeded; closing active connections");
      server.closeAllConnections();
    }, 10_000);
    timeout.unref();
    server.close(async error => {
      clearTimeout(timeout);
      try { await prisma.$disconnect(); }
      catch (disconnectError) { console.error("Prisma disconnect failed", disconnectError); process.exitCode = 1; }
      if (error) { console.error("HTTP shutdown failed", error); process.exitCode = 1; }
    });
  };
  process.once("SIGINT", () => shutdown("SIGINT"));
  process.once("SIGTERM", () => shutdown("SIGTERM"));
}

startServer().catch(async error => {
  console.error("Failed to start API:", error);
  await prisma.$disconnect().catch(() => {});
  process.exitCode = 1;
});
