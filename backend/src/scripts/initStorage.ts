import { CreateBucketCommand, HeadBucketCommand } from "@aws-sdk/client-s3";
import { s3, bucket } from "../config/storage.js";

async function main() {
  try {
    await s3.send(new HeadBucketCommand({ Bucket: bucket }));
    console.log(`Storage bucket already exists: ${bucket}`);
  } catch (error) {
    const status = (error as { $metadata?: { httpStatusCode?: number } }).$metadata?.httpStatusCode;
    if (status !== 404) throw error;
    await s3.send(new CreateBucketCommand({ Bucket: bucket }));
    console.log(`Created storage bucket: ${bucket}`);
  } finally {
    s3.destroy();
  }
}
main().catch(error => { console.error("Storage setup failed:", error); process.exitCode = 1; });
