import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';

import { api, clearStoredAccessCode } from '@/api/client';
import { login } from '@/copy/he';
import { Button } from '@/design/Button';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

type LoginError = 'none' | 'empty' | 'wrong';

export function Login() {
  useDocumentTitle(login.title);
  const navigate = useNavigate();
  const [code, setCode] = useState('');
  const [error, setError] = useState<LoginError>('none');
  const [busy, setBusy] = useState(false);

  // Arriving here means signed out: the access code lives in sessionStorage only and is dropped.
  useEffect(() => {
    clearStoredAccessCode();
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (code.trim() === '') {
      setError('empty');
      return;
    }
    setBusy(true);
    try {
      const result = await api.login(code);
      if (result.ok) navigate('/upload');
      else setError('wrong');
    } finally {
      setBusy(false);
    }
  }

  const errorText =
    error === 'empty' ? login.errorNoCode : error === 'wrong' ? login.errorWrongCode : null;

  return (
    <main className="mx-auto flex min-h-screen max-w-[420px] flex-col justify-center gap-4 px-4 py-6">
      <section className="flex flex-col gap-4 rounded-lg border border-surface-border bg-surface-raised p-5 shadow-card">
        <header>
          <h1>{login.title}</h1>
          <p className="mt-1 text-ink-muted">{login.subtitle}</p>
        </header>
        <p className="rounded-md bg-state-neutral-bg p-3 text-sm text-state-neutral-fg">
          {login.demoBanner} {login.demoBannerLocalOnly}
        </p>
        <form onSubmit={(event) => void onSubmit(event)} noValidate className="flex flex-col gap-3">
          <label htmlFor="access-code" className="text-sm font-medium text-ink-strong">
            {login.codeLabel}
          </label>
          <input
            id="access-code"
            name="access-code"
            type="password"
            autoComplete="off"
            dir="ltr"
            value={code}
            placeholder={login.codePlaceholder}
            aria-invalid={errorText !== null}
            aria-describedby={errorText ? 'access-code-error' : undefined}
            onChange={(event) => setCode(event.target.value)}
            className="min-h-10 rounded-md border border-ink-muted bg-surface-raised px-3 text-start text-base text-ink-strong placeholder:text-ink-muted"
          />
          {errorText ? (
            <p id="access-code-error" role="alert" className="text-sm text-state-review-fg">
              {errorText}
            </p>
          ) : null}
          <Button type="submit" loading={busy}>
            {login.submit}
          </Button>
        </form>
      </section>
    </main>
  );
}
