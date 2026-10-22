import { upload } from '@/copy/he';

/**
 * Client pre-check limits. They mirror the server's (Settings) so the user is told early;
 * the server stays authoritative and re-checks everything.
 */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
/** Accepted by extension, because browsers report an empty or wrong MIME type for some scans. */
export const ACCEPTED_EXTENSIONS: readonly string[] = ['.pdf', '.jpg', '.jpeg', '.png', '.zip'];
export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.join(',');

export type Rejection = 'type' | 'size';

export interface FileCheck {
  accepted: File[];
  rejected: { file: File; reason: Rejection }[];
}

/** Why a file is refused, or null when it passes. Type is checked before size. */
export function checkFile(file: File): Rejection | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((extension) => name.endsWith(extension))) return 'type';
  if (file.size > MAX_UPLOAD_BYTES) return 'size';
  return null;
}

export function checkFiles(files: readonly File[]): FileCheck {
  const result: FileCheck = { accepted: [], rejected: [] };
  for (const file of files) {
    const reason = checkFile(file);
    if (reason === null) result.accepted.push(file);
    else result.rejected.push({ file, reason });
  }
  return result;
}

/** Hebrew rejection message; it states what happened and the limit. */
export function rejectionMessage(reason: Rejection): string {
  return reason === 'type' ? upload.rejectedType : upload.tooLarge;
}
