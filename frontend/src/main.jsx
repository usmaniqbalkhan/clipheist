import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { Analytics } from '@vercel/analytics/react';
import App from './App.jsx';
import { AuthProvider } from './components/AuthContext.jsx';
import './styles/landing.css';
import './styles/site.css';

createRoot(document.getElementById('root')).render(
  <BrowserRouter>
    <AuthProvider>
      <App />
      <Analytics />
    </AuthProvider>
  </BrowserRouter>
);
