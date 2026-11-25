-- Phase 4.3C: additive shadow index metadata ONLY.
-- No data migration, vector publication, READY transition or index switch.
-- queryEnabled is false for all new records and no production code reads it.

CREATE TABLE `ShadowIndexCandidate` (
  `documentVersionId` VARCHAR(191) NOT NULL,
  `generationDigest` CHAR(64) NOT NULL,
  `organizationId` VARCHAR(191) NOT NULL,
  `knowledgeBaseId` VARCHAR(191) NOT NULL,
  `documentId` VARCHAR(191) NOT NULL,
  `jobId` VARCHAR(191) NOT NULL,
  `sourceSha256` CHAR(64) NOT NULL,
  `recordsSha256` CHAR(64) NOT NULL,
  `passageCount` INTEGER NOT NULL,
  `verifiedAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `revokedAt` DATETIME(3) NULL,

  INDEX `ShadowIndexCandidate_organizationId_knowledgeBaseId_idx`(`organizationId`, `knowledgeBaseId`),
  INDEX `ShadowIndexCandidate_documentVersionId_revokedAt_idx`(`documentVersionId`, `revokedAt`),
  PRIMARY KEY (`documentVersionId`, `generationDigest`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE `ShadowIndexSelection` (
  `documentVersionId` VARCHAR(191) NOT NULL,
  `generationDigest` CHAR(64) NULL,
  `revision` BIGINT NOT NULL DEFAULT 0,
  `queryEnabled` BOOLEAN NOT NULL DEFAULT false,
  `updatedAt` DATETIME(3) NOT NULL,
  CONSTRAINT `ShadowIndexSelection_queryEnabled_disabled_ck` CHECK (`queryEnabled` = 0),
  CONSTRAINT `ShadowIndexSelection_revision_nonnegative_ck` CHECK (`revision` >= 0),

  PRIMARY KEY (`documentVersionId`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

ALTER TABLE `ShadowIndexCandidate`
  ADD CONSTRAINT `ShadowIndexCandidate_documentVersionId_fkey`
  FOREIGN KEY (`documentVersionId`) REFERENCES `DocumentVersion`(`id`)
  ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE `ShadowIndexSelection`
  ADD CONSTRAINT `ShadowIndexSelection_documentVersionId_fkey`
  FOREIGN KEY (`documentVersionId`) REFERENCES `DocumentVersion`(`id`)
  ON DELETE CASCADE ON UPDATE CASCADE;
