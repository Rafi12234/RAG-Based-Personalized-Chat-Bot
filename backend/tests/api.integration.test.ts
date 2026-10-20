import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import app from "../src/app.js";
import { prisma } from "../src/config/prisma.js";
import { queuedVersionData } from "../src/lib/ingestion.js";

let server: Server;
let baseUrl = "";
const users: string[] = [];
const organizations: string[] = [];
const suffix = randomUUID().slice(0, 10);

type ApiResponse = {
  status: number;
  body: any;
  cookie: string;
};

async function request(
  path: string,
  method = "GET",
  cookie = "",
  payload?: object | FormData,
  options: { origin?: string; omitRequestHeader?: boolean } = {},
): Promise<ApiResponse> {
  const isForm = payload instanceof FormData;
  const headers: Record<string, string> = {
    origin: options.origin ?? "http://localhost:5173",
  };
  if (!options.omitRequestHeader) {
    headers["x-docmind-request"] = "1";
  }
  if (cookie) {
    headers.cookie = cookie;
  }
  if (payload && !isForm) {
    headers["content-type"] = "application/json";
  }

  const response = await fetch(baseUrl + "/api/v1" + path, {
    method,
    headers,
    body: payload ? (isForm ? payload : JSON.stringify(payload)) : undefined,
  });

  return {
    status: response.status,
    body: await response.json(),
    cookie: (response.headers.get("set-cookie") || "").split(";")[0],
  };
}

async function register(label: string) {
  const response = await request("/auth/register", "POST", "", {
    name: "Integration " + label,
    email: "phase1-" + label + "-" + suffix + "@example.invalid",
    password: "LocalTestPassword!2026",
    organizationName: "Test " + label,
  });

  assert.equal(response.status, 201, JSON.stringify(response.body));
  users.push(response.body.user.id);
  organizations.push(response.body.user.memberships[0].organizationId);
  assert.ok(response.cookie.startsWith("docmind_session="));

  return {
    cookie: response.cookie,
    user: response.body.user,
    orgId: response.body.user.memberships[0].organizationId,
  };
}

before(async () => {
  await prisma.$connect();
  server = app.listen(0);
  await new Promise<void>((resolve) => server.once("listening", resolve));
  baseUrl = "http://127.0.0.1:" + (server.address() as AddressInfo).port;
});

after(async () => {
  try {
    if (organizations.length) {
      const docIds = (
        await prisma.document.findMany({
          where: { knowledgeBase: { organizationId: { in: organizations } } },
          select: { id: true },
        })
      ).map((d) => d.id);

      if (docIds.length) {
        await prisma.ingestionJob.deleteMany({
          where: { documentVersion: { documentId: { in: docIds } } },
        });
        await prisma.documentVersion.deleteMany({
          where: { documentId: { in: docIds } },
        });
        await prisma.document.deleteMany({
          where: { id: { in: docIds } },
        });
      }

      await prisma.knowledgeBase.deleteMany({
        where: { organizationId: { in: organizations } },
      });
      await prisma.organizationMember.deleteMany({
        where: { organizationId: { in: organizations } },
      });
      await prisma.organization.deleteMany({
        where: { id: { in: organizations } },
      });
    }

    if (users.length) {
      await prisma.authSession.deleteMany({
        where: { userId: { in: users } },
      });
      await prisma.user.deleteMany({
        where: { id: { in: users } },
      });
    }
  } finally {
    if (server) {
      await new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      );
    }
    await prisma.$disconnect();
  }
});

test("Phase 1 API: sessions, roles, isolation, safe upload rejection, archive and retention", async () => {
  const alice = await register("alice");
  const bob = await register("bob");

  assert.equal((await request("/auth/me")).status, 401);
  assert.equal((await request("/auth/me", "GET", alice.cookie)).status, 200);

  const badOrigin = await request(
    "/organizations",
    "POST",
    alice.cookie,
    { name: "Not created" },
    { origin: "https://attacker.invalid" },
  );
  assert.equal(badOrigin.status, 403);
  assert.equal(
    (
      await request(
        "/organizations",
        "POST",
        alice.cookie,
        { name: "Missing header" },
        { omitRequestHeader: true },
      )
    ).status,
    403,
  );

  const created = await request("/knowledge-bases", "POST", alice.cookie, {
    organizationId: alice.orgId,
    name: "Private research",
    description: "Integration test",
  });
  assert.equal(created.status, 201, JSON.stringify(created.body));
  const kbId: string = created.body.knowledgeBase.id;

  assert.equal(
    (await request("/knowledge-bases?organizationId=" + alice.orgId, "GET", bob.cookie)).status,
    404,
  );
  assert.equal((await request("/knowledge-bases/" + kbId + "/documents", "GET", bob.cookie)).status, 404);
  assert.equal((await request("/knowledge-bases/" + kbId, "PATCH", bob.cookie, { name: "Stolen" })).status, 404);
  assert.equal((await request("/knowledge-bases/" + kbId, "DELETE", bob.cookie)).status, 404);

  // Reject unauthorized uploads before Multer accepts the file.
  const form = new FormData();
  form.append("file", new Blob(["%PDF-1.7\n"]), "unauthorized.pdf");
  assert.equal(
    (await request("/knowledge-bases/" + kbId + "/documents", "POST", bob.cookie, form)).status,
    404,
  );

  const seeded = await prisma.document.create({
    data: {
      title: "sample.txt",
      knowledgeBaseId: kbId,
      uploadedById: alice.user.id,
      versions: {
        create: queuedVersionData({
          version: 1,
          filename: "sample.txt",
          mimeType: "text/plain",
          sizeBytes: 6n,
          storageKey: "tests/" + randomUUID(),
        }),
      },
    },
  });

  // Phase 2.1A: DocumentVersion and IngestionJob must be created together.
  const queuedVersion = await prisma.documentVersion.findFirst({
    where: { documentId: seeded.id },
    include: { jobs: true },
  });
  assert.ok(queuedVersion, "Document version must exist");
  assert.equal(queuedVersion.status, "QUEUED");
  assert.equal(queuedVersion.jobs.length, 1);
  assert.equal(queuedVersion.jobs[0].status, "QUEUED");
  assert.equal(queuedVersion.jobs[0].attempts, 0);

  assert.equal((await request("/documents/" + seeded.id + "/download", "GET", bob.cookie)).status, 404);
  assert.equal((await request("/documents/" + seeded.id, "DELETE", bob.cookie)).status, 404);

  const addBob = await request(
    "/organizations/" + alice.orgId + "/members",
    "POST",
    alice.cookie,
    { email: bob.user.email, role: "MEMBER" },
  );
  assert.equal(addBob.status, 201, JSON.stringify(addBob.body));
  assert.equal((await request("/knowledge-bases/" + kbId + "/documents", "GET", bob.cookie)).status, 200);
  assert.equal(
    (
      await request("/knowledge-bases", "POST", bob.cookie, {
        organizationId: alice.orgId,
        name: "Unpermitted",
      })
    ).status,
    403,
  );
  assert.equal((await request("/knowledge-bases/" + kbId, "PATCH", bob.cookie, { name: "Unpermitted" })).status, 403);
  assert.equal((await request("/knowledge-bases/" + kbId, "DELETE", bob.cookie)).status, 403);
  // Members can view documents but cannot archive another user's upload.
  assert.equal((await request("/documents/" + seeded.id, "DELETE", bob.cookie)).status, 403);

  const archive = await request("/documents/" + seeded.id, "DELETE", alice.cookie);
  assert.equal(archive.status, 200, JSON.stringify(archive.body));
  assert.equal((await request("/documents/" + seeded.id + "/download", "GET", bob.cookie)).status, 404);

  // Archived documents still have history and cannot be silently purged.
  const kbDelete = await request("/knowledge-bases/" + kbId, "DELETE", alice.cookie);
  assert.equal(kbDelete.status, 409);
  assert.match(kbDelete.body.message, /archived documents/);

  const emptyKb = await request("/knowledge-bases", "POST", alice.cookie, {
    organizationId: alice.orgId,
    name: "Empty collection",
  });
  assert.equal(emptyKb.status, 201);
  const emptyId = emptyKb.body.knowledgeBase.id;
  assert.equal(
    (await request("/knowledge-bases/" + emptyId, "PATCH", alice.cookie, { name: "Renamed collection" })).status,
    200,
  );
  assert.equal((await request("/knowledge-bases/" + emptyId, "DELETE", alice.cookie)).status, 200);

  const loggedOut = await request("/auth/logout", "POST", alice.cookie);
  assert.equal(loggedOut.status, 200);
  assert.equal((await request("/auth/me", "GET", alice.cookie)).status, 401);

  const login = await request("/auth/login", "POST", "", {
    email: alice.user.email,
    password: "LocalTestPassword!2026",
  });
  assert.equal(login.status, 200);
  assert.ok(login.cookie.startsWith("docmind_session="));
  assert.equal((await request("/auth/me", "GET", login.cookie)).status, 200);
});
