import { S3Client } from "@aws-sdk/client-s3";
import { env } from "./env.js";

export const s3 = new S3Client({
  endpoint: env.S3_ENDPOINT,
  region: env.S3_REGION,
  forcePathStyle: true,
  credentials: { accessKeyId: env.S3_ACCESS_KEY, secretAccessKey: env.S3_SECRET_KEY },
});
export const bucket = env.S3_BUCKET;
