import { Navigate } from 'react-router-dom';
import type { RouteObject } from 'react-router-dom';

import { Layout } from '@/components/Layout';
import { Dashboard } from '@/pages/Dashboard';
import { Export } from '@/pages/Export';
import { Login } from '@/pages/Login';
import { Processing } from '@/pages/Processing';
import { Review } from '@/pages/Review';
import { Upload } from '@/pages/Upload';

/** The six screens. Everything except login shares the app shell. */
export const routes: RouteObject[] = [
  { path: '/', element: <Navigate to="/login" replace /> },
  { path: '/login', element: <Login /> },
  {
    element: <Layout />,
    children: [
      { path: '/upload', element: <Upload /> },
      { path: '/jobs/:jobId/processing', element: <Processing /> },
      { path: '/jobs/:jobId/review', element: <Review /> },
      { path: '/jobs/:jobId/export', element: <Export /> },
      { path: '/dashboard', element: <Dashboard /> },
    ],
  },
  { path: '*', element: <Navigate to="/login" replace /> },
];
