
import app from "./app.js";
import { env } from "./config/env.js";
import { prisma } from "./config/prisma.js";

async function startServer() {
  // Verify database connectivity before starting HTTP server
  await prisma.$connect();

  console.log("Database connection established");

  const server = app.listen(env.PORT, () => {
    console.log(
      `DocMind-AI API running at http://localhost:${env.PORT}`,
    );
  });

  let shuttingDown = false;

  function shutdown(signal: string) {
    if (shuttingDown) return;

    shuttingDown = true;
    console.log(`Received ${signal}. Shutting down...`);

    server.close(async (error) => {
      let exitCode = error ? 1 : 0;

      if (error) {
        console.error("HTTP shutdown error:", error);
      }

      try {
        await prisma.$disconnect();
        console.log("Database disconnected");
      } catch (disconnectError) {
        console.error(
          "Database disconnect failed:",
          disconnectError,
        );
        exitCode = 1;
      }

      process.exit(exitCode);
    });

    server.closeAllConnections();
  }

  process.once("SIGINT", () => shutdown("SIGINT"));
  process.once("SIGTERM", () => shutdown("SIGTERM"));
}

startServer().catch(async (error) => {
  console.error("Unable to start DocMind-AI API:", error);

  await prisma.$disconnect().catch(() => {});

  process.exitCode = 1;
});
