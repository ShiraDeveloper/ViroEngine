import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import { useCallback, useRef } from 'react';

import { api } from '@/api/client';
import type { ApiClient } from '@/api/client';
import type { CommitmentRow, FieldEditRequest, FieldKey, JobView } from '@/api/types';
import { review } from '@/copy/he';
import { useToast } from '@/design/Toast';
import { jobQueryKey } from '@/lib/useJob';
import { applyOptimisticEdit, replaceRow } from '@/lib/review';

export type ReviewClient = Pick<ApiClient, 'editField' | 'undo' | 'confirm'>;

interface EditVariables {
  rowId: string;
  field: FieldKey;
  req: FieldEditRequest;
}

interface EditContext {
  previous: CommitmentRow | undefined;
}

function setRow(queryClient: QueryClient, jobId: string, row: CommitmentRow): void {
  queryClient.setQueryData<JobView>(jobQueryKey(jobId), (job) =>
    job ? { ...job, rows: replaceRow(job.rows, row) } : job,
  );
}

export interface ReviewActions {
  edit(rowId: string, field: FieldKey, req: FieldEditRequest): void;
  undo(): void;
  confirm(): Promise<boolean>;
  isConfirming: boolean;
}

/**
 * Edit, undo and confirm against the job cache. Edits are optimistic so the keyboard flow never
 * waits on a round trip; a failed edit restores that one row and says so in a toast.
 */
export function useReviewMutations(jobId: string, client: ReviewClient = api): ReviewActions {
  const queryClient = useQueryClient();
  const toast = useToast();
  // Rows edited in this session, newest last. Ctrl+Z walks it back through the API.
  const edited = useRef<string[]>([]);

  const editMutation = useMutation<CommitmentRow, Error, EditVariables, EditContext>({
    mutationFn: ({ rowId, field, req }) => client.editField(jobId, rowId, field, req),
    onMutate: async ({ rowId, field, req }) => {
      await queryClient.cancelQueries({ queryKey: jobQueryKey(jobId) });
      const job = queryClient.getQueryData<JobView>(jobQueryKey(jobId));
      const previous = job?.rows.find((row) => row.row_id === rowId);
      if (previous) setRow(queryClient, jobId, applyOptimisticEdit(previous, field, req));
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) setRow(queryClient, jobId, context.previous);
      edited.current.pop();
      toast.push({ tone: 'error', title: review.editFailedTitle, description: review.editFailedBody });
    },
    onSuccess: (row) => setRow(queryClient, jobId, row),
  });

  const undoMutation = useMutation<CommitmentRow, Error, string>({
    mutationFn: (rowId) => client.undo(jobId, rowId),
    onSuccess: (row) => {
      setRow(queryClient, jobId, row);
      toast.push({ tone: 'info', title: review.undoneTitle });
    },
    onError: (_error, rowId) => {
      edited.current.push(rowId);
      toast.push({ tone: 'error', title: review.undoFailedTitle });
    },
  });

  const confirmMutation = useMutation<JobView, Error>({
    mutationFn: () => client.confirm(jobId),
    onSuccess: (job) => queryClient.setQueryData<JobView>(jobQueryKey(jobId), job),
  });

  const edit = useCallback(
    (rowId: string, field: FieldKey, req: FieldEditRequest) => {
      edited.current.push(rowId);
      editMutation.mutate({ rowId, field, req });
    },
    [editMutation],
  );

  const undo = useCallback(() => {
    const rowId = edited.current.pop();
    if (rowId === undefined) {
      toast.push({ tone: 'info', title: review.nothingToUndo });
      return;
    }
    undoMutation.mutate(rowId);
  }, [toast, undoMutation]);

  const confirm = useCallback(async () => {
    try {
      await confirmMutation.mutateAsync();
      toast.push({ tone: 'success', title: review.confirmDone });
      return true;
    } catch {
      // A failed confirm leaves the job as it was; the toast says what to do next.
      toast.push({ tone: 'error', title: review.confirmFailed });
      return false;
    }
  }, [confirmMutation, toast]);

  return { edit, undo, confirm, isConfirming: confirmMutation.isPending };
}
