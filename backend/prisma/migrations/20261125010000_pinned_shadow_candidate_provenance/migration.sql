-- Phase 4.3E: optional immutable provenance for NEW pinned shadow candidates.
-- Existing rows stay NULL and cannot be used by the isolated reader.
-- The Phase 4.3C queryEnabled=0 CHECK remains unchanged.
ALTER TABLE ShadowIndexCandidate
    ADD COLUMN modelPinSha256 CHAR(64) NULL,
    ADD COLUMN attemptTokenSha256 CHAR(64) NULL,
    ADD COLUMN verifiedAttempt INT NULL,
    ADD CONSTRAINT ShadowIndexCandidate_pin_all_or_none_ck CHECK (
        (modelPinSha256 IS NULL AND attemptTokenSha256 IS NULL AND verifiedAttempt IS NULL)
        OR (modelPinSha256 IS NOT NULL AND attemptTokenSha256 IS NOT NULL
            AND verifiedAttempt IS NOT NULL AND verifiedAttempt > 0)
    );
