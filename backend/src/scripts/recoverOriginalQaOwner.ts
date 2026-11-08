/**
 * Local-only, operator-assisted recovery for the Phase 3 QA workspace owner.
 *
 * No HTTP endpoint, no privilege changes, no password in argv/environment/logs.
 * Only usable from an interactive terminal against a loopback development DB
 * with an exact workspace confirmation and explicit opt-in. Requires operator
 * control of local database credentials (not suitable for production).
 */
import "dotenv/config";
import argon2 from "argon2";
import { createInterface, emitKeypressEvents } from "node:readline";
import type { Key } from "node:readline";

const WORKSPACE_ID = "cmvw9o9b8000248l1l6oqtgye";
const OPT_IN = "DOCMIND_ALLOW_LOCAL_OWNER_RECOVERY";
const SAFE_VALUE = "YES_FOR_LOCAL_TESTING";

function requireLocalOperator(): void {
  if (process.env.NODE_ENV !== "development" ||
      !["127.0.0.1", "localhost", "::1"].includes(process.env.DATABASE_HOST ?? "") ||
      process.env[OPT_IN] !== SAFE_VALUE ||
      process.argv.length !== 4 ||
      process.argv[2] !== "--confirm-workspace-id" ||
      process.argv[3] !== WORKSPACE_ID ||
      !process.stdin.isTTY ||
      !process.stdout.isTTY ||
      typeof process.stdin.setRawMode !== "function") {
    throw new Error("Local owner recovery refused: developer TTY, explicit opt-in, loopback DB and exact QA workspace confirmation required.");
  }
}

async function ask(question: string): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve, reject) => {
    rl.question(question, answer => {
      rl.close();
      resolve(answer.trim());
    });
    rl.on("SIGINT", () => {
      rl.close();
      reject(new Error("Canceled"));
    });
  });
}

/** Read secret from a real terminal without echoing or displaying characters. */
async function readHidden(question: string): Promise<string> {
  process.stdout.write(question);
  const input = process.stdin;
  const originalRaw = input.isRaw;
  emitKeypressEvents(input);
  input.setRawMode(true);
  input.resume();
  return new Promise((resolve, reject) => {
    let result = "";
    const cleanup = () => {
      input.off("keypress", onKey);
      input.setRawMode(Boolean(originalRaw));
      input.pause();
      process.stdout.write("\n");
    };
    const onKey = (value: string, key: Key) => {
      if (key.ctrl && key.name === "c") {
        cleanup();
        reject(new Error("Canceled"));
      } else if (key.name === "return" || key.name === "enter") {
        cleanup();
        resolve(result);
      } else if (key.name === "backspace") {
        result = result.slice(0, -1);
      } else if (!key.ctrl && !key.meta &&
                 value && !/[\x00-\x1f\x7f]/u.test(value) && result.length < 129) {
        result += value;
      }
    };
    input.on("keypress", onKey);
  });
}

async function main(): Promise<void> {
  requireLocalOperator();
  const { prisma } = await import("../config/prisma.js");
  try {
    console.log("Local developer recovery for the original Phase 3 QA workspace.");
    console.log("This revokes existing sessions of the recovered owner. It does not change memberships or documents.");
    const email = (await ask("Enter the ORIGINAL workspace owner's full email (not shown here): ")).toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/u.test(email) || email.length > 191) {
      throw new Error("Invalid email.");
    }
    const owner = await prisma.user.findUnique({
      where: { email },
      select: {
        id: true, name: true, isActive: true,
        memberships: {
          where: { organizationId: WORKSPACE_ID, role: "OWNER" },
          select: { organizationId: true },
        },
      },
    });
    if (!owner || !owner.isActive || owner.memberships.length !== 1) {
      throw new Error("This account is not the active owner of the expected original QA workspace.");
    }
    console.log(\`Verified the workspace OWNER account: \${owner.name}.\`);
    const confirm = await ask("Type RECOVER ORIGINAL QA OWNER to confirm the password reset: ");
    if (confirm !== "RECOVER ORIGINAL QA OWNER") {
      throw new Error("Confirmation did not match. Nothing was changed.");
    }
    const password = await readHidden("New owner password (12-128 chars, hidden): ");
    if (password.length < 12 || password.length > 128) {
      throw new Error("New password must be 12-128 characters. Nothing was changed.");
    }
    const repeat = await readHidden("Re-enter new password (hidden): ");
    if (password !== repeat) {
      throw new Error("Passwords did not match. Nothing was changed.");
    }
    const passwordHash = await argon2.hash(password, { type: argon2.argon2id });
    await prisma.$transaction(async tx => {
      // Re-check exact active ownership before committing.
      const ownerMembership = await tx.organizationMember.findUnique({
        where: { userId_organizationId: { userId: owner.id, organizationId: WORKSPACE_ID } },
        select: { role: true },
      });
      if (ownerMembership?.role !== "OWNER") {
        throw new Error("Workspace ownership changed; nothing was changed.");
      }
      await tx.user.update({ where: { id: owner.id }, data: { passwordHash } });
      await tx.authSession.deleteMany({ where: { userId: owner.id } });
    });
    console.log("Owner password updated and existing owner sessions revoked.");
    console.log("Use an Incognito window to sign in as the owner. Your existing other-account session is unaffected.");
  } finally {
    await prisma.$disconnect();
  }
}

main().catch(error => {
  console.error(error instanceof Error ? error.message : "Local recovery failed.");
  process.exitCode = 1;
});
