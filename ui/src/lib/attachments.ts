import type { AttachmentAdapter, CompleteAttachment, PendingAttachment } from "@assistant-ui/react";

/**
 * Attaching anything, not just pictures.
 *
 * The composer used `SimpleImageAttachmentAdapter`, which accepts `image/*` and nothing else,
 * and even the images never arrived. Its `send()` puts the data URL on
 * `attachment.content` — while the code that built the request read `message.content` and then
 * skipped images in the attachments list, on the assumption the first pass had caught them.
 * Dropped twice. No conversation transcript on this machine has ever carried an attachment,
 * which is the whole feature: a paperclip that reads a file and throws it away.
 *
 * Two things this deliberately does not do:
 *
 * **It does not restrict by type.** A spreadsheet, a PDF, a zip, a log — he has a whole
 * computer and can open any of them; refusing the attachment is refusing the more capable
 * route, not the less. The server decides what to inline for the model and what to hand him
 * as a file on disk.
 *
 * **It does not check whether the model can see images.** That check belongs on the server,
 * next to the model config, and the answer is not "offer or don't offer" — it is *how* the
 * attachment reaches him. On a model with no vision an image should still arrive, as a file he
 * can open, rather than the button vanishing and the picture being unmentionable.
 */
export class AnyFileAttachmentAdapter implements AttachmentAdapter {
  accept = "*";

  async add({ file }: { file: File }): Promise<PendingAttachment> {
    return {
      id: `${Date.now()}-${file.name}`,
      type: file.type.startsWith("image/") ? "image" : "file",
      name: file.name,
      contentType: file.type || "application/octet-stream",
      file,
      status: { type: "requires-action", reason: "composer-send" },
    } as PendingAttachment;
  }

  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    // A data URL rather than a raw ArrayBuffer: it survives JSON, it is what an <img> and the
    // OpenAI-shaped image_url field both take, and the server can decode it once to write the
    // file. Large files pay for that in base64 overhead, which is the trade for one code path.
    const data = await asDataUrl(attachment.file!);
    return {
      ...attachment,
      status: { type: "complete" },
      content: [
        attachment.type === "image"
          ? { type: "image", image: data }
          : // A non-image gets a text part naming it, because that is what the composer
            // renders in the sent message. The bytes travel separately — see wireAttachments.
            { type: "text", text: `[attached ${attachment.name}]` },
      ],
      // Kept for the wire step. `content` cannot carry arbitrary bytes in a shape
      // assistant-ui will render, and inventing a part type it does not know breaks the
      // composer's own preview.
      ...({ kithData: data } as Record<string, unknown>),
    } as CompleteAttachment;
  }

  async remove(): Promise<void> {}
}

async function asDataUrl(file: File): Promise<string> {
  if (typeof FileReader === "undefined") {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 32768) {
      binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
    }
    return `data:${file.type || "application/octet-stream"};base64,${btoa(binary)}`;
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
