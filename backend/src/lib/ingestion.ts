
type QueuedVersionInput = {
  version: number;
  filename: string;
  mimeType: string;
  sizeBytes: bigint;
  storageKey: string;
};

/**
 * Create a document version and its initial ingestion job
 * together through Prisma's nested relational write.
 *
 * No Redis dispatch happens at this stage.
 */
export function queuedVersionData(input: QueuedVersionInput) {
  return {
    ...input,
    status: "QUEUED" as const,
    jobs: {
      create: {
        status: "QUEUED" as const,
      },
    },
  };
}
