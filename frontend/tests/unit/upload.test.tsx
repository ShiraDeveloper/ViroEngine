import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/client';
import type { UploadResponse } from '@/api/types';
import { upload } from '@/copy/he';
import { MAX_UPLOAD_BYTES } from '@/lib/uploadValidation';
import { Upload } from '@/pages/Upload';

type UploadClient = NonNullable<Parameters<typeof Upload>[0]>['client'];

const RESPONSE: UploadResponse = { job_id: 'job-1', status: 'queued', page_count: 1 };

function fakeClient(overrides: Partial<NonNullable<UploadClient>> = {}): NonNullable<UploadClient> {
  return {
    uploadSingle: vi.fn(() => Promise.resolve(RESPONSE)),
    uploadBatch: vi.fn(() => Promise.resolve(RESPONSE)),
    ...overrides,
  };
}

function renderUpload(client: NonNullable<UploadClient>) {
  return render(
    <MemoryRouter initialEntries={['/upload']}>
      <Routes>
        <Route path="/upload" element={<Upload client={client} />} />
        <Route path="/jobs/:jobId/processing" element={<p>processing-page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

function fileInput(container: HTMLElement): HTMLInputElement {
  const input = container.querySelector('input[type="file"]');
  if (!(input instanceof HTMLInputElement)) throw new Error('file input missing');
  return input;
}

function choose(container: HTMLElement, ...files: File[]): void {
  fireEvent.change(fileInput(container), { target: { files } });
}

function pdf(name = 'form.pdf'): File {
  return new File(['%PDF'], name, { type: 'application/pdf' });
}

describe('upload screen', () => {
  it('rejects_unsupported_type_with_hebrew_message', () => {
    const { container } = renderUpload(fakeClient());
    choose(container, new File(['x'], 'notes.txt', { type: 'text/plain' }));
    expect(screen.getByRole('alert')).toHaveTextContent(upload.rejectedType);
    expect(screen.queryByRole('button', { name: upload.start })).toBeNull();
  });

  it('rejects_oversized_file_with_limit_in_message', () => {
    const { container } = renderUpload(fakeClient());
    const big = pdf('big.pdf');
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD_BYTES + 1 });
    choose(container, big);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(upload.tooLarge);
    expect(alert).toHaveTextContent('50MB');
  });

  it('batch_mode_accepts_multiple_files', async () => {
    const client = fakeClient();
    const { container } = renderUpload(client);
    fireEvent.click(screen.getByRole('button', { name: upload.batch }));
    expect(fileInput(container)).toHaveAttribute('multiple');
    choose(container, pdf('a.pdf'), pdf('b.pdf'), pdf('c.pdf'));
    expect(screen.getByText(upload.selectedCount(3))).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: upload.start }));
    await waitFor(() => expect(client.uploadBatch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(client.uploadBatch).mock.calls[0]?.[0]).toHaveLength(3);
    expect(client.uploadSingle).not.toHaveBeenCalled();
    expect(await screen.findByText('processing-page')).toBeInTheDocument();
  });

  it('generates_idempotency_key_per_attempt', async () => {
    const uploadSingle = vi
      .fn<(file: File, key: string) => Promise<UploadResponse>>()
      .mockRejectedValueOnce(new ApiError(500))
      .mockResolvedValueOnce(RESPONSE);
    const { container } = renderUpload(fakeClient({ uploadSingle }));
    choose(container, pdf());
    fireEvent.click(screen.getByRole('button', { name: upload.start }));
    expect(await screen.findByText(upload.failed)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: upload.start }));
    await waitFor(() => expect(uploadSingle).toHaveBeenCalledTimes(2));
    const keys = uploadSingle.mock.calls.map(([, key]) => key);
    expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
    expect(keys[1]).toMatch(/^[0-9a-f-]{36}$/);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it('double_click_does_not_create_two_jobs', () => {
    const uploadSingle = vi.fn(() => new Promise<UploadResponse>(() => undefined));
    const { container } = renderUpload(fakeClient({ uploadSingle }));
    choose(container, pdf());
    const start = screen.getByRole('button', { name: upload.start });
    fireEvent.click(start);
    fireEvent.click(start);
    expect(uploadSingle).toHaveBeenCalledTimes(1);
  });
});
