-- AlterTable
ALTER TABLE `IngestionJob` ADD COLUMN `dispatchAttempts` INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN `dispatchLeaseUntil` DATETIME(3) NULL,
    ADD COLUMN `nextAttemptAt` DATETIME(3) NULL,
    ADD COLUMN `processingLeaseUntil` DATETIME(3) NULL,
    ADD COLUMN `processingToken` VARCHAR(64) NULL;

-- CreateIndex
CREATE INDEX `IngestionJob_status_nextAttemptAt_dispatchLeaseUntil_idx` ON `IngestionJob`(`status`, `nextAttemptAt`, `dispatchLeaseUntil`);

-- CreateIndex
CREATE INDEX `IngestionJob_status_processingLeaseUntil_idx` ON `IngestionJob`(`status`, `processingLeaseUntil`);
