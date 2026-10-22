import { ClipboardCheck, Download, Microscope, ScanLine, UploadCloud } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';

import { IS_MOCK } from '@/api/client';
import { common, nav, upload } from '@/copy/he';
import { getCurrentJobId } from '@/lib/session';

const LINK_BASE =
  'rounded-pill px-3 py-1 text-sm font-medium transition-colors duration-fast ease-token';

function linkClass({ isActive }: { isActive: boolean }): string {
  return isActive
    ? `${LINK_BASE} bg-surface-raised text-primary-900 shadow-card`
    : `${LINK_BASE} text-ink-inverse/80 hover:bg-ink-inverse/10 hover:text-ink-inverse`;
}

interface Step {
  key: string;
  to: string;
  label: string;
  icon: LucideIcon;
}

/** Index of the workflow step the current route belongs to; -1 off the workflow. */
function currentStep(pathname: string): number {
  if (pathname.startsWith('/upload')) return 0;
  if (pathname.endsWith('/processing')) return 1;
  if (pathname.endsWith('/review')) return 2;
  if (pathname.endsWith('/export')) return 3;
  return -1;
}

/** Decorative molecule pattern in the header. Pure ornament: hidden from assistive tech. */
function HeaderPattern() {
  return (
    <svg
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 h-full w-full text-brand-glow opacity-20"
      preserveAspectRatio="xMidYMid slice"
      viewBox="0 0 800 160"
    >
      <g fill="none" stroke="currentColor" strokeWidth="1.2">
        <circle cx="90" cy="40" r="22" />
        <circle cx="90" cy="40" r="6" fill="currentColor" />
        <path d="M90 18v-10M90 62v10M68 40h-10M112 40h10M74 24l-7-7M106 56l7 7M74 56l-7 7M106 24l7-7" />
        <circle cx="300" cy="120" r="14" />
        <circle cx="300" cy="120" r="4" fill="currentColor" />
        <path d="M300 106v-8M300 134v8M286 120h-8M314 120h8" />
        <circle cx="560" cy="30" r="10" />
        <circle cx="690" cy="110" r="26" />
        <circle cx="690" cy="110" r="7" fill="currentColor" />
        <path d="M690 84v-12M690 136v12M664 110h-12M716 110h12M672 92l-8-8M708 128l8 8M672 128l-8 8M708 92l8-8" />
        <path d="M112 40 C 180 40, 220 120, 286 120 M314 120 C 420 120, 480 30, 550 30 M570 30 C 620 30, 640 110, 664 110" strokeDasharray="3 6" />
      </g>
    </svg>
  );
}

/** App shell: skip link, branded banner with navigation, the workflow stepper, main, footer. */
export function Layout() {
  const { pathname } = useLocation();
  const jobId = encodeURIComponent(getCurrentJobId());
  const steps: Step[] = [
    { key: 'upload', to: '/upload', label: nav.upload, icon: UploadCloud },
    { key: 'processing', to: `/jobs/${jobId}/processing`, label: nav.processing, icon: ScanLine },
    { key: 'review', to: `/jobs/${jobId}/review`, label: nav.review, icon: ClipboardCheck },
    { key: 'export', to: `/jobs/${jobId}/export`, label: nav.export, icon: Download },
  ];
  const active = currentStep(pathname);

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:start-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-surface-raised focus:p-3"
      >
        {common.skipToContent}
      </a>
      <header className="relative overflow-hidden bg-gradient-to-l from-brand-to via-brand-via to-brand-from text-ink-inverse">
        <HeaderPattern />
        <div className="relative mx-auto flex max-w-screen-xl flex-wrap items-center justify-between gap-4 px-5 pb-8 pt-4">
          <NavLink to="/upload" className="flex items-center gap-3 rounded-md">
            <span className="grid size-10 place-items-center rounded-md bg-ink-inverse/10 ring-1 ring-ink-inverse/25">
              <Microscope aria-hidden="true" className="size-5 text-brand-glow" />
            </span>
            <span className="flex flex-col leading-tight">
              <span className="text-lg font-bold">{common.appName}</span>
              <span className="text-xs text-ink-inverse/70">{common.appTagline}</span>
            </span>
          </NavLink>
          <nav aria-label={nav.label} className="flex flex-wrap items-center gap-1">
            <NavLink to="/login" className={linkClass}>
              {nav.logout}
            </NavLink>
          </nav>
        </div>
      </header>
      {IS_MOCK ? (
        <p className="bg-state-neutral-bg px-5 py-2 text-center text-sm text-state-neutral-fg">
          {common.mockBanner}
        </p>
      ) : null}
      {active >= 0 ? (
        <div className="relative z-10 mx-auto -mt-5 w-full max-w-screen-xl px-5">
          <ol
            aria-label={upload.stepsLabel}
            className="grid grid-cols-4 gap-1 rounded-lg border border-surface-border bg-surface-raised p-1 shadow-raised"
          >
            {steps.map((step, index) => {
              const Icon = step.icon;
              const state = index < active ? 'done' : index === active ? 'current' : 'next';
              const tone =
                state === 'current'
                  ? 'bg-primary-900 text-ink-inverse'
                  : state === 'done'
                    ? 'text-teal-700'
                    : 'text-ink-muted';
              return (
                <li key={step.key} aria-current={state === 'current' ? 'step' : undefined}>
                  <NavLink
                    to={step.to}
                    className={`flex items-center justify-center gap-2 rounded-md px-2 py-2 text-sm font-medium transition-colors duration-fast hover:bg-surface-sunken ${tone}`}
                  >
                  <span
                    aria-hidden="true"
                    className={`grid size-6 place-items-center rounded-full text-xs ${
                      state === 'current'
                        ? 'bg-brand-glow text-primary-900'
                        : state === 'done'
                          ? 'bg-teal-100 text-teal-700'
                          : 'bg-surface-sunken'
                    }`}
                  >
                    <Icon className="size-3.5" />
                  </span>
                  <span className="hidden sm:inline">{step.label}</span>
                  </NavLink>
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
      <main id="main" className="mx-auto w-full max-w-screen-xl flex-1 px-5 py-6">
        <Outlet />
      </main>
      <footer className="border-t border-surface-border bg-surface-raised">
        <div className="mx-auto flex max-w-screen-xl flex-wrap items-center justify-between gap-2 px-5 py-4 text-xs text-ink-muted">
          <span>{common.footer}</span>
          <span>{common.footerPrivacy}</span>
        </div>
      </footer>
    </div>
  );
}
