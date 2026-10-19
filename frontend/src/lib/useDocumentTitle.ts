import { useEffect } from 'react';

import { common } from '@/copy/he';

/** Set the tab title for the current screen: "<screen> · <app name>". */
export function useDocumentTitle(screen: string): void {
  useEffect(() => {
    document.title = `${screen} · ${common.appName}`;
  }, [screen]);
}
