import { Microscope, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';

import { api, clearStoredAccessCode } from '@/api/client';
import { common, login, upload } from '@/copy/he';
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
    <main className="grid min-h-screen bg-surface-base lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden overflow-hidden bg-gradient-to-bl from-brand-from via-brand-via to-brand-to p-8 text-ink-inverse lg:flex lg:flex-col lg:justify-between">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-24 -start-24 size-96 rounded-full bg-brand-glow/20 blur-3xl"
        />
        <div className="relative flex items-center gap-3">
          <span className="grid size-12 place-items-center rounded-lg bg-ink-inverse/10 ring-1 ring-ink-inverse/25">
            <Microscope aria-hidden="true" className="size-6 text-brand-glow" />
          </span>
          <span className="text-xl font-bold">{common.appName}</span>
        </div>
        <div className="relative flex max-w-lg flex-col gap-4">
          <p className="text-4xl font-bold leading-tight">{login.heroTitle}</p>
          <p className="text-lg text-ink-inverse/80">{login.heroBody}</p>
          <ul className="mt-2 flex flex-col gap-2">
            {upload.features.map((feature) => (
              <li key={feature.title} className="flex items-start gap-2 text-ink-inverse/90">
                <ShieldCheck aria-hidden="true" className="mt-1 size-4 shrink-0 text-brand-glow" />
                <span>
                  <strong className="font-semibold">{feature.title}</strong> · {feature.body}
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-ink-inverse/60">{common.footer}</p>
      </section>
      <div className="flex flex-col justify-center px-4 py-8">
        <section className="mx-auto flex w-full max-w-[420px] flex-col gap-4 rounded-xl border border-surface-border bg-surface-raised p-6 shadow-raised">
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
              className="min-h-11 rounded-md border border-surface-border bg-surface-base px-3 text-start text-base text-ink-strong placeholder:text-ink-muted focus:border-primary-500"
            />
            {errorText ? (
              <p id="access-code-error" role="alert" className="text-sm text-state-review-fg">
                {errorText}
              </p>
            ) : null}
            <Button type="submit" size="lg" loading={busy}>
              {login.submit}
            </Button>
          </form>
        </section>
      </div>
    </main>
  );
}
