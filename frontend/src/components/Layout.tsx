import { NavLink, Outlet } from 'react-router-dom';

import { IS_MOCK } from '@/api/client';
import { common, nav } from '@/copy/he';
import { getCurrentJobId } from '@/lib/session';

const LINK_BASE = 'rounded-md px-3 py-2 text-sm font-medium transition-colors duration-fast';

function linkClass({ isActive }: { isActive: boolean }): string {
  return isActive
    ? `${LINK_BASE} bg-primary-100 text-primary-900`
    : `${LINK_BASE} text-ink-base hover:bg-surface-sunken`;
}

/** App shell: skip link, banner with primary navigation, and the main landmark. */
export function Layout() {
  const jobId = encodeURIComponent(getCurrentJobId());
  const links = [
    { to: '/upload', label: nav.upload },
    { to: `/jobs/${jobId}/processing`, label: nav.processing },
    { to: `/jobs/${jobId}/review`, label: nav.review },
    { to: `/jobs/${jobId}/export`, label: nav.export },
    { to: '/dashboard', label: nav.dashboard },
  ];

  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:start-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-surface-raised focus:p-3"
      >
        {common.skipToContent}
      </a>
      <header className="border-b border-surface-border bg-surface-raised">
        <div className="mx-auto flex max-w-screen-xl items-center justify-between gap-5 px-5 py-3">
          <p className="text-lg font-bold text-primary-900">{common.appName}</p>
          <nav aria-label={nav.label} className="flex flex-wrap items-center gap-2">
            {links.map((link) => (
              <NavLink key={link.to} to={link.to} className={linkClass}>
                {link.label}
              </NavLink>
            ))}
            <NavLink to="/login" className={linkClass}>
              {nav.logout}
            </NavLink>
          </nav>
        </div>
        {IS_MOCK ? (
          <p className="bg-state-neutral-bg px-5 py-2 text-center text-sm text-state-neutral-fg">
            {common.mockBanner}
          </p>
        ) : null}
      </header>
      <main id="main" className="mx-auto max-w-screen-xl px-5 py-6">
        <Outlet />
      </main>
    </>
  );
}
