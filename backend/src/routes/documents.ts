import { randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import { open, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { Router } from "express";
import multer from "multer";
import { rateLimit } from "express-rate-limit";
import { Upload } from "@aws-sdk/lib-storage";
import { DeleteObjectCommand, GetObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { prisma } from "../config/prisma.js";
import { s3, bucket } from "../config/storage.js";
import { HttpError } from "../lib/http.js";
import { queuedVersionData } from "../lib/ingestion.js";
import { currentUserId, requireAuth } from "../middleware/auth.js";
import { accessibleKnowledgeBase, membershipFor } from "../middleware/organization.js";

const MAX_UPLOAD = 250 * 1024 * 1024;
const upload = multer({
  dest: tmpdir(),
  limits: { fileSize: MAX_UPLOAD, files: 1, fields: 0 },
});
const uploadLimiter = rateLimit({
  windowMs: 60 * 60_000,
  limit: 40,
  standardHeaders: "draft-8",
  legacyHeaders: false,
});

const types: Record<string, string> = {
  ".pdf": "application/pdf",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".txt": "text/plain; charset=utf-8",
};

export function supportedFilename(filename: string): boolean {
  const basename = path.posix.basename(filename.replace(/\\/g, "/"));
  return (
    basename.length > 0 &&
    basename.length <= 200 &&
    Object.hasOwn(types, path.extname(basename).toLowerCase())
  );
}

async function checkFile(
  file: Express.Multer.File,
): Promise<{ filename: string; contentType: string }> {
  const filename = path.posix.basename(file.originalname.replace(/\\/g, "/"));

  if (!supportedFilename(filename)) {
    throw new HttpError(400, "Only PDF, DOCX and TXT files are supported (filename up to 200 characters)");
  }
  if (file.size === 0) {
    throw new HttpError(400, "Empty files are not allowed");
  }

  const header = Buffer.alloc(8);
  const handle = await open(file.path, "r");
  let bytesRead = 0;

  try {
    ({ bytesRead } = await handle.read(header, 0, header.length, 0));
  } finally {
    await handle.close();
  }

  const ext = path.extname(filename).toLowerCase();
  if (ext === ".pdf" && header.subarray(0, 5).toString("ascii") !== "%PDF-") {
    throw new HttpError(400, "File does not appear to be a PDF");
  }

  // DOCX is a ZIP-based OOXML format. Detailed archive validation is part of ingestion.
  if (ext === ".docx" && header.subarray(0, 4).toString("hex") !== "504b0304") {
    throw new HttpError(400, "File does not appear to be a DOCX archive");
  }
  if (ext === ".txt" && header.subarray(0, bytesRead).includes(0)) {
    throw new HttpError(400, "Text file appears to contain binary content");
  }

  return { filename, contentType: types[ext] };
}

function serializeDocument(document: {
  id: string;
  title: string;
  createdAt: Date;
  uploadedById: string;
  versions: {
    id: string;
    filename: string;
    sizeBytes: bigint;
    status: string;
    version: number;
    createdAt: Date;
  }[];
}) {
  const latest = document.versions[0];
  return {
    id: document.id,
    title: document.title,
    createdAt: document.createdAt,
    uploadedById: document.uploadedById,
    latestVersion: latest
      ? { ...latest, sizeBytes: Number(latest.sizeBytes) }
      : null,
  };
}

export const knowledgeBaseDocumentsRouter = Router();
knowledgeBaseDocumentsRouter.use(requireAuth);

knowledgeBaseDocumentsRouter.get("/:knowledgeBaseId/documents", async (req, res) => {
  const knowledgeBaseId = String(req.params.knowledgeBaseId);
  await accessibleKnowledgeBase(currentUserId(req), knowledgeBaseId);

  const documents = await prisma.document.findMany({
    where: { knowledgeBaseId, deletedAt: null },
    include: {
      versions: {
        orderBy: { version: "desc" },
        take: 1,
        select: {
          id: true,
          filename: true,
          sizeBytes: true,
          status: true,
          version: true,
          createdAt: true,
        },
      },
    },
    orderBy: { createdAt: "desc" },
    take: 100,
  });

  res.json({ success: true, documents: documents.map(serializeDocument) });
});

// Reject unauthorized knowledge bases BEFORE Multer writes the request body to disk.
knowledgeBaseDocumentsRouter.post(
  "/:knowledgeBaseId/documents",
  uploadLimiter,
  async (req, _res, next) => {
    try {
      await accessibleKnowledgeBase(currentUserId(req), String(req.params.knowledgeBaseId));
      next();
    } catch (error) {
      next(error);
    }
  },
  upload.single("file"),
  async (req, res) => {
    const knowledgeBaseId = String(req.params.knowledgeBaseId);
    const file = req.file;
    if (!file) {
      throw new HttpError(400, "A file field named 'file' is required");
    }

    let storageKey: string | undefined;
    try {
      const { kb } = await accessibleKnowledgeBase(currentUserId(req), knowledgeBaseId);
      const { filename, contentType } = await checkFile(file);
      storageKey = `organizations/${kb.organizationId}/knowledge-bases/${kb.id}/${randomUUID()}`;

      await new Upload({
        client: s3,
        params: {
          Bucket: bucket,
          Key: storageKey,
          Body: createReadStream(file.path),
          ContentType: contentType,
          ContentLength: file.size,
        },
        queueSize: 2,
        partSize: 8 * 1024 * 1024,
        leavePartsOnError: false,
      }).done();

      try {
        // Prisma's nested write atomically creates the document, version and job.
        const document = await prisma.document.create({
          data: {
            title: filename,
            knowledgeBaseId,
            uploadedById: currentUserId(req),
            versions: {
              create: queuedVersionData({
                version: 1,
                filename,
                mimeType: contentType,
                sizeBytes: BigInt(file.size),
                storageKey,
              }),
            },
          },
          include: {
            versions: {
              select: {
                id: true,
                filename: true,
                sizeBytes: true,
                status: true,
                version: true,
                createdAt: true,
              },
            },
          },
        });

        res.status(201).json({
          success: true,
          document: serializeDocument(document),
          message: "File stored and ingestion job recorded for background processing.",
        });
      } catch (dbError) {
        // Prevent untracked objects when metadata creation fails.
        await s3.send(new DeleteObjectCommand({ Bucket: bucket, Key: storageKey })).catch(() => {});
        throw dbError;
      }
    } finally {
      await unlink(file.path).catch(() => {});
    }
  },
);

export const documentsRouter = Router();
documentsRouter.use(requireAuth);

async function permittedDocument(userId: string, id: string) {
  const document = await prisma.document.findFirst({
    where: { id, deletedAt: null },
    include: { knowledgeBase: { select: { organizationId: true } } },
  });
  if (!document) {
    throw new HttpError(404, "Document not found");
  }
  const membership = await membershipFor(userId, document.knowledgeBase.organizationId);
  return { document, membership };
}

documentsRouter.get("/:documentId/download", async (req, res) => {
  const { document } = await permittedDocument(currentUserId(req), String(req.params.documentId));
  const version = await prisma.documentVersion.findFirst({
    where: { documentId: document.id },
    orderBy: { version: "desc" },
  });
  if (!version) {
    throw new HttpError(404, "Document version not found");
  }

  const asciiName = version.filename.replace(/[^a-zA-Z0-9._-]/g, "_");
  const url = await getSignedUrl(
    s3,
    new GetObjectCommand({
      Bucket: bucket,
      Key: version.storageKey,
      ResponseContentDisposition: `attachment; filename="${asciiName}"`,
    }),
    { expiresIn: 60 },
  );

  res.setHeader("Cache-Control", "no-store");
  res.json({ success: true, url, expiresInSeconds: 60 });
});

documentsRouter.delete("/:documentId", async (req, res) => {
  const { document, membership } = await permittedDocument(currentUserId(req), String(req.params.documentId));
  if (membership.role === "MEMBER" && document.uploadedById !== currentUserId(req)) {
    throw new HttpError(403, "Only the uploader or a workspace manager can archive this document");
  }

  // Phase 1 intentionally uses logical deletion. A separate retention/purge job
  // must delete object-store bytes later; archived documents are not served.
  await prisma.document.update({
    where: { id: document.id },
    data: { deletedAt: new Date() },
  });
  res.json({ success: true, message: "Document archived" });
});
