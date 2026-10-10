# Public media links (optional)

Instagram images and Stories, and everything on Threads, are fetched by Meta from a **public URL**.
The studio offers two free ways to provide one:

| `[hosting] provider` | Setup | Handles | How it works |
|---|---|---|---|
| `facebook` (default) | none, once Facebook is connected | images | uploads the image to your Page as an **unpublished** photo, passes its CDN link to Meta, deletes it after posting |
| `s3` | a free bucket, ~10 minutes | images and video | uploads to a **private** bucket, passes a **presigned** link that expires in `url_ttl_minutes` (60), deletes the file right after posting |

You need `s3` only for **Threads videos**, or if you prefer not to route images through your Page.

## Backblaze B2 (10 GB free)

1. Create a Backblaze account at <https://www.backblaze.com/cloud-storage> and open **B2 Cloud Storage**.
2. **Buckets → Create a Bucket:** a unique name, Files **Private**. Note the **Endpoint** shown on the
   bucket, e.g. `s3.us-west-004.backblazeb2.com`. The region is the part after `s3.`.
3. **Application Keys → Add a New Application Key:** allow access to **this bucket only**, with
   Read and Write. Copy the **keyID** and **applicationKey** (shown once).
4. In `studio.toml`:

```toml
[hosting]
provider = "s3"
endpoint = "https://s3.us-west-004.backblazeb2.com"
region = "us-west-004"
bucket = "your-bucket-name"
```

5. In the notebook, run `social("auth", "hosting")` and paste both keys into the hidden prompts. Then
   run `social("doctor", "--online")`: it uploads a tiny file, fetches it through a presigned link and
   deletes it.

B2's free tier covers the first 10 GB of storage, plus egress up to three times your average stored
amount. Files live here only for minutes, so a studio uses almost none of it.

Any other S3-compatible store works the same way (Cloudflare R2, MinIO, Wasabi). Signing is AWS SigV4
with presigned query links.
