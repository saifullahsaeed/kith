"use client";

import { type PropsWithChildren, useEffect, useState, type FC } from "react";
import { XIcon, FileText, Loader2Icon, AlertCircleIcon } from "lucide-react";
import {
  AttachmentPrimitive,
  MessagePrimitive,
  useAuiState,
  useAui,
} from "@assistant-ui/react";
import { useShallow } from "zustand/shallow";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Dialog, DialogTitle, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { isPastedFile } from "@/components/assistant-ui/composer-input/paste";
import { cn } from "@/lib/utils";

const useFileSrc = (file: File | undefined) => {
  const [src, setSrc] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!file) {
      setSrc(undefined);
      return;
    }

    const objectUrl = URL.createObjectURL(file);
    setSrc(objectUrl);

    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [file]);

  return src;
};

const useAttachmentSrc = () => {
  const { file, src } = useAuiState(
    useShallow((s): { file?: File; src?: string } => {
      if (s.attachment.type !== "image") return {};
      if (s.attachment.file) return { file: s.attachment.file };
      const src = s.attachment.content?.filter((c) => c.type === "image")[0]?.image;
      if (!src) return {};
      return { src };
    }),
  );

  return useFileSrc(file) ?? src;
};

type AttachmentPreviewProps = {
  src: string;
};

const AttachmentPreview: FC<AttachmentPreviewProps> = ({ src }) => {
  const [isLoaded, setIsLoaded] = useState(false);
  return (
    <img
      src={src}
      alt="Attachment preview"
      className={cn(
        "block h-auto max-h-[80vh] w-auto max-w-full rounded-sm object-contain transition-opacity duration-300 motion-reduce:transition-none",
        isLoaded
          ? "aui-attachment-preview-image-loaded opacity-100"
          : "aui-attachment-preview-image-loading opacity-0",
      )}
      onLoad={() => setIsLoaded(true)}
    />
  );
};

const AttachmentPreviewDialog: FC<PropsWithChildren> = ({ children }) => {
  const src = useAttachmentSrc();

  if (!src) return children;

  return (
    <Dialog>
      <DialogTrigger className="aui-attachment-preview-trigger cursor-zoom-in" asChild>
        {children}
      </DialogTrigger>
      <DialogContent className="aui-attachment-preview-dialog-content [&>button]:bg-foreground/60 [&>button]:hover:bg-foreground/80 [&_svg]:text-background p-2 sm:max-w-3xl [&>button]:rounded-full [&>button]:p-1 [&>button]:opacity-100 [&>button]:ring-0!">
        <DialogTitle className="aui-sr-only sr-only">Image Attachment Preview</DialogTitle>
        <div className="aui-attachment-preview bg-background relative mx-auto flex max-h-[80dvh] w-full items-center justify-center overflow-hidden rounded-sm">
          <AttachmentPreview src={src} />
        </div>
      </DialogContent>
    </Dialog>
  );
};

const AttachmentThumb: FC = () => {
  const src = useAttachmentSrc();

  return (
    <Avatar className="aui-attachment-tile-avatar h-full w-full rounded-none">
      <AvatarImage
        src={src}
        alt="Attachment preview"
        className="aui-attachment-tile-image object-cover"
      />
      <AvatarFallback>
        <FileText className="aui-attachment-tile-fallback-icon text-muted-foreground/80 size-6 stroke-[1.5]" />
      </AvatarFallback>
    </Avatar>
  );
};

/**
 * A block of text that was pasted rather than typed.
 *
 * Pasting forty thousand characters of a log into the composer makes the composer useless — you
 * cannot see what you are writing, and the sentence you meant to send is buried at the bottom of
 * someone else's output. So a large paste is lifted out of the message and shown here instead.
 *
 * A preview rather than an icon, because the whole question in the half-second after pasting is
 * "did it take the right thing", and a 56-pixel square with a document glyph on it cannot answer
 * that. The badge says PASTED for the same reason: this did not come from the file picker and
 * calling it `pasted-1.txt` in a tooltip would be the app naming something the person didn't.
 *
 * It still reaches him in full — the server inlines text attachments into the message
 * (`_with_attachments`). This is about where the text sits on screen, not whether it is sent.
 */
const PastedCard: FC<{ isComposer: boolean }> = ({ isComposer }) => {
  const file = useAuiState((s): File | undefined => s.attachment.file);
  const [preview, setPreview] = useState("");

  useEffect(() => {
    if (!file) return;
    let alive = true;
    // Only the head of it. Reading a ten-megabyte paste in full to show six lines would block the
    // composer at exactly the moment it has to feel instant.
    void file
      .slice(0, 2_000)
      .text()
      .then((text) => alive && setPreview(text));
    return () => {
      alive = false;
    };
  }, [file]);

  const lines = preview.split("\n").filter((line, index) => index < 8 || line.trim());

  return (
    <AttachmentPrimitive.Root className="aui-attachment-root animate-in fade-in-0 zoom-in-95 relative duration-200 motion-reduce:animate-none">
      <div className="bg-muted/60 border-border/60 relative w-56 overflow-hidden rounded-[calc(var(--composer-radius)-var(--composer-padding))] border p-2">
        <p className="text-muted-foreground/80 line-clamp-6 font-mono text-[10px] leading-[1.45] break-all whitespace-pre-wrap">
          {lines.slice(0, 8).join("\n") || "…"}
        </p>
        <span className="border-border/70 text-muted-foreground bg-background/80 mt-1.5 inline-block rounded-md border px-1.5 py-0.5 text-[9px] font-medium tracking-wider">
          PASTED
        </span>
      </div>
      {isComposer && <AttachmentRemove />}
    </AttachmentPrimitive.Root>
  );
};

export const AttachmentUI: FC = () => {
  const aui = useAui();
  const isComposer = aui.attachment.source !== "message";
  const isPasted = useAuiState((s) => isPastedFile(s.attachment.name ?? ""));

  const isImage = useAuiState((s) => s.attachment.type === "image");
  const typeLabel = useAuiState((s) => {
    const type = s.attachment.type;
    switch (type) {
      case "image":
        return "Image";
      case "document":
        return "Document";
      case "file":
        return "File";
      default:
        return type;
    }
  });

  const uploadState = useAuiState((s) =>
    s.attachment.status.type === "running"
      ? "uploading"
      : s.attachment.status.type === "incomplete" && s.attachment.status.reason === "error"
        ? "error"
        : undefined,
  );
  const isUploading = uploadState === "uploading";
  const isError = uploadState === "error";

  const errorMessage = useAuiState((s) =>
    s.attachment.status.type === "incomplete" && s.attachment.status.reason === "error"
      ? (s.attachment.status.message ?? "Upload failed")
      : undefined,
  );

  if (isPasted) return <PastedCard isComposer={isComposer} />;

  return (
    <Tooltip>
      <AttachmentPrimitive.Root
        className={cn(
          "aui-attachment-root relative",
          isComposer && "animate-in fade-in-0 zoom-in-95 duration-200 motion-reduce:animate-none",
          isImage && !isComposer && "aui-attachment-root-message only:*:first:size-24",
        )}
      >
        <AttachmentPreviewDialog>
          <TooltipTrigger asChild>
            <div
              className={cn(
                "aui-attachment-tile bg-muted hover:after:bg-foreground/10 focus-visible:ring-ring/50 relative size-14 cursor-pointer overflow-hidden rounded-[calc(var(--composer-radius)-var(--composer-padding))] transition-transform outline-none after:pointer-events-none after:absolute after:inset-0 after:rounded-[inherit] after:ring-1 after:ring-black/10 after:transition-colors after:ring-inset focus-visible:ring-3 active:scale-[0.96] motion-reduce:transition-none dark:after:ring-white/10",
                isError && "after:ring-destructive/60 dark:after:ring-destructive/60",
              )}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  e.currentTarget.click();
                } else if (e.key === " ") {
                  e.preventDefault();
                }
              }}
              onKeyUp={(e) => {
                if (e.key === " ") e.currentTarget.click();
              }}
              aria-label={`${typeLabel} attachment${
                isError ? ", upload failed" : isUploading ? ", uploading" : ""
              }`}
            >
              <AttachmentThumb />
              {isUploading && (
                <div
                  aria-hidden="true"
                  className="aui-attachment-tile-uploading bg-background/60 animate-in fade-in-0 absolute inset-0 flex items-center justify-center backdrop-blur-[2px] motion-reduce:animate-none"
                >
                  <Loader2Icon className="text-muted-foreground size-4 animate-spin" />
                </div>
              )}
              {isError && (
                <div
                  aria-hidden="true"
                  className="aui-attachment-tile-error bg-background/70 animate-in fade-in-0 absolute inset-0 flex items-center justify-center backdrop-blur-[2px] motion-reduce:animate-none"
                >
                  <AlertCircleIcon className="text-destructive size-4" />
                </div>
              )}
            </div>
          </TooltipTrigger>
        </AttachmentPreviewDialog>
        {isComposer && <AttachmentRemove />}
      </AttachmentPrimitive.Root>
      <TooltipContent side="top">
        <AttachmentPrimitive.Name />
        {errorMessage && <p className="aui-attachment-error-message">{errorMessage}</p>}
      </TooltipContent>
    </Tooltip>
  );
};

const AttachmentRemove: FC = () => {
  return (
    <AttachmentPrimitive.Remove asChild>
      <TooltipIconButton
        tooltip="Remove file"
        className="aui-attachment-tile-remove absolute end-1 top-1 size-5 rounded-full bg-black/50! text-white backdrop-blur-sm after:absolute after:-inset-1.5 hover:bg-black/70! hover:text-white! active:scale-[0.96] motion-reduce:transition-none"
        side="top"
      >
        <XIcon className="aui-attachment-remove-icon size-3 stroke-[2.5]" />
      </TooltipIconButton>
    </AttachmentPrimitive.Remove>
  );
};

export const UserMessageAttachments: FC = () => {
  return (
    <div className="aui-user-message-attachments-end col-span-full col-start-1 row-start-1 flex w-full flex-row justify-end gap-2">
      <MessagePrimitive.Attachments>{() => <AttachmentUI />}</MessagePrimitive.Attachments>
    </div>
  );
};

// `ComposerAttachments` and `ComposerAddAttachment` were here, both unreferenced. Each had a
// live counterpart in thread.tsx that had quietly replaced it — `ComposerAttachmentStrip`,
// which adds the empty-state guard and the wrapping, and the paperclip in the composer bar.
// Two versions of one component with only one of them rendered is how you spend an evening
// styling the wrong file.
