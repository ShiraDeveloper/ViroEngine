/** Job opened by the demo links when no upload has happened in this tab. */
export const DEMO_JOB_ID = 'demo';

const JOB_KEY = 'safeline.jobId';

export function getCurrentJobId(): string {
  return sessionStorage.getItem(JOB_KEY) ?? DEMO_JOB_ID;
}

export function setCurrentJobId(jobId: string): void {
  sessionStorage.setItem(JOB_KEY, jobId);
}
